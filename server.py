"""Run the multithreaded TCP file server."""

import argparse
from contextlib import contextmanager
import ipaddress
from pathlib import Path, PureWindowsPath
import socket
import socketserver
from tempfile import NamedTemporaryFile
from threading import Lock
from time import perf_counter

from analytics import Metrics
from protocol import (ProtocolError, file_size, read_message, receive_file,
                      send_file, send_message)

TEMP_PREFIX = ".transfer-"


class RequestError(Exception):
    """An operation failed and the connection can still accept commands."""


def storage_path(root, name):
    if not isinstance(name, str) or not name:
        raise RequestError("Enter a path relative to the shared folder.")
    parts = name.split("/")
    if any(part in ("", ".", "..") or part.startswith(TEMP_PREFIX)
           or PureWindowsPath(part).is_reserved()
           or part.endswith((" ", "."))
           or any(ord(char) < 32 or char in '\\:*?"<>|' for char in part)
           for part in parts):
        raise RequestError("Use a valid relative path, such as media/file.wav.")
    path = root.joinpath(*parts)
    resolved = path.resolve()
    if resolved != path:
        raise RequestError("Shared paths must refer to ordinary files or folders.")
    return path


class FileHandler(socketserver.StreamRequestHandler):
    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        while True:
            try:
                request = read_message(self.rfile)
            except (EOFError, OSError, ProtocolError):
                return

            self.started = perf_counter()
            self.response = self.transfer = self.size = None
            command = request.get("command")
            path = request.get("path", "")
            outcome = "error"
            close = False
            try:
                outcome = self.dispatch(command, path, request) or "ok"
            except (RequestError, OSError, ValueError, RuntimeError) as error:
                # Once a response begins, a stream error may leave file bytes pending.
                if self.response is None:
                    try:
                        self.reply("error", message=str(error))
                    except OSError:
                        close = True
                else:
                    close = True
            except (EOFError, ProtocolError):
                close = True
            finally:
                self.server.metrics.record(
                    peer, command, path, outcome=outcome,
                    total=perf_counter() - self.started, response=self.response,
                    size=self.size, transfer=self.transfer
                )
            if close or command == "quit":
                return

    def reply(self, status, **fields):
        send_message(self.request, status=status, **fields)
        if self.response is None:
            self.response = perf_counter() - self.started

    def dispatch(self, command, name, request):
        root = self.server.root
        if command in ("connect", "quit"):
            self.reply("ok")
            return

        if command == "dir":
            folder = storage_path(root, name) if name else root
            with self.server.storage_lock:
                entries = [
                    (entry.name, entry.is_dir())
                    for entry in sorted(folder.iterdir())
                    if not entry.name.startswith(TEMP_PREFIX) and not entry.is_symlink()
                ]
            self.reply("ok", count=len(entries))
            for name, is_directory in entries:
                send_message(self.request, name=name, directory=is_directory)
            return

        if command not in ("upload", "download", "delete", "mkdir", "rmdir"):
            raise RequestError("Unknown command.")

        with self.server.storage_lock:
            path = storage_path(root, name)

        if command in ("upload", "download"):
            with self.reserve(path):
                if command == "upload":
                    completed = self.upload(path, file_size(request))
                else:
                    self.download(path)
                    completed = True
            self.reply("ok" if completed else "cancelled")
            return "ok" if completed else "cancelled"

        with self.server.storage_lock:
            if path in self.server.active_paths:
                raise RequestError("The file is currently being processed.")
            if command == "delete":
                if not path.is_file():
                    raise RequestError("File does not exist.")
                path.unlink()
            elif command == "mkdir":
                path.mkdir()
            elif command == "rmdir":
                if any(path in active.parents for active in self.server.active_paths):
                    raise RequestError("A file in this folder is currently being processed.")
                path.rmdir()

        self.reply("ok")

    @contextmanager
    def reserve(self, path):
        with self.server.storage_lock:
            if path in self.server.active_paths:
                raise RequestError("The file is currently being processed.")
            self.server.active_paths.add(path)
        try:
            yield
        finally:
            with self.server.storage_lock:
                self.server.active_paths.remove(path)

    def upload(self, path, size):
        if not path.parent.is_dir():
            raise RequestError("Destination folder does not exist.")
        if path.exists():
            if not path.is_file():
                raise RequestError("Destination is not a regular file.")
            self.reply("overwrite")
            answer = read_message(self.rfile).get("overwrite")
            if answer is not True:
                return False

        temporary = None
        try:
            # A completed upload replaces the destination in one filesystem operation.
            with NamedTemporaryFile(dir=path.parent, prefix=TEMP_PREFIX,
                                    delete=False) as output:
                temporary = Path(output.name)
                self.reply("ready")
                started = perf_counter()
                receive_file(self.rfile, output, size)
            temporary.replace(path)
            self.transfer = perf_counter() - started
            self.size = size
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True

    def download(self, path):
        if not path.is_file():
            raise RequestError("File does not exist.")
        with path.open("rb") as source:
            size = path.stat().st_size
            self.reply("ok", size=size)
            started = perf_counter()
            send_file(self.request, source, size)
            self.transfer = perf_counter() - started
            self.size = size
            if read_message(self.rfile).get("status") != "ok":
                raise ProtocolError("Client did not confirm the download.")


class FileServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, root, metrics):
        self.root = root
        self.metrics = metrics
        self.storage_lock = Lock()
        self.active_paths = set()
        super().__init__(address, FileHandler)


def local_addresses():
    addresses = set()
    for host in (socket.gethostname(), socket.getfqdn()):
        try:
            addresses.update(socket.gethostbyname_ex(host)[2])
        except OSError:
            pass
    # A UDP connect asks the OS which local address it would use. It sends no packets.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(address for address in addresses
                  if not ipaddress.ip_address(address).is_loopback)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="IPv4 address to bind")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--root", type=Path, default=Path("shared_files"))
    parser.add_argument("--log", type=Path, default=Path("server_metrics.csv"))
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.log.resolve().is_relative_to(root):
            parser.error("Choose a metrics log outside the shared folder.")
        root.mkdir(parents=True, exist_ok=True)
        with FileServer((args.host, args.port), root, Metrics(args.log, "server")) as server:
            host, port = server.server_address
            addresses = local_addresses() if host == "0.0.0.0" else [host]
            print(f"Sharing {root} on {host}:{port}", flush=True)
            for address in addresses or ["127.0.0.1"]:
                print(f"Client command: connect {address} {port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except (OSError, OverflowError) as error:
        parser.exit(1, f"Server error: {error}\n")


if __name__ == "__main__":
    main()

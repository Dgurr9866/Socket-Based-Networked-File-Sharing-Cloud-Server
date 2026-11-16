"""Run the Tkinter desktop client for the TCP file server."""

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
from queue import Empty, Queue
import socket
from tempfile import NamedTemporaryFile
from threading import Thread
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from analytics import Metrics
from protocol import (ProtocolError, file_size, read_message, receive_file,
                      send_file, send_message)


class CommandError(Exception):
    """Report an error without dropping the session."""


class Client:
    def __init__(self, metrics):
        self.metrics = metrics
        self.report = print
        self.entries = None
        self.session = ExitStack()
        self.connection = None
        self.peer = ""

    def disconnect(self):
        self.session.close()
        self.connection = None

    def connect(self, host, port):
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise CommandError("Enter a port number from 1 to 65535.")
        self.peer = f"{host}:{port}"
        self.disconnect()
        connection = self.session.enter_context(socket.create_connection((host, int(port)), 10))
        self.reader = self.session.enter_context(connection.makefile("rb"))
        self.connection = connection
        send_message(connection, command="connect")
        self.expect(self.reply(), "ok")
        connection.settimeout(None)
        self.report(f"Connected to {self.peer}")

    def reply(self):
        message = read_message(self.reader)
        if self.response is None:
            self.response = perf_counter() - self.started
        if message.get("status") == "error":
            raise CommandError(message.get("message", "Server rejected the request."))
        return message

    @staticmethod
    def expect(message, status):
        if message.get("status") != status:
            raise ProtocolError(f"Expected server status {status!r}.")

    def execute(self, command, arguments):
        self.outcome = "error"
        self.entries = None
        if self.connection is None and command != "connect":
            self.report("Connect to a server first.")
            return

        path = arguments[0] if arguments and command != "connect" else ""
        if command == "upload":
            path = arguments[1]
        elif command == "subfolder":
            action, path = arguments
            command = "mkdir" if action == "create" else "rmdir"

        self.started = perf_counter()
        self.response = self.transfer = self.size = None
        outcome = "error"
        try:
            if command == "connect":
                self.connect(*arguments)
            elif command == "upload":
                if not self.upload(Path(arguments[0]), path):
                    outcome = "cancelled"
                    return
            elif command == "download":
                self.download(path, Path(arguments[1]))
            else:
                self.simple_command(command, path)
            outcome = "ok"
        except CommandError as error:
            if command == "connect":
                self.disconnect()
            self.report(f"Error: {error}")
        except (EOFError, OSError, ProtocolError) as error:
            self.disconnect()
            self.report(f"Connection or transfer failed: {error}")
        finally:
            self.outcome = outcome
            self.metrics.record(
                self.peer, command, path, outcome=outcome,
                total=perf_counter() - self.started, response=self.response,
                size=self.size, transfer=self.transfer
            )

    def simple_command(self, command, path):
        send_message(self.connection, command=command, path=path)
        message = self.reply()
        self.expect(message, "ok")
        if command == "dir":
            count = message.get("count")
            if type(count) is not int or count < 0:
                raise ProtocolError("Invalid directory entry count.")
            entries = []
            for _ in range(count):
                entry = read_message(self.reader)
                name, directory = entry.get("name"), entry.get("directory")
                if not isinstance(name, str) or type(directory) is not bool:
                    raise ProtocolError("Invalid directory entry.")
                entries.append((name, directory))
            self.entries = entries
        else:
            self.report(f"OK: {path}")

    def upload(self, source_path, remote_path):
        try:
            if not source_path.is_file():
                raise OSError(f"Local file does not exist: {source_path}")
            source = source_path.open("rb")
        except OSError as error:
            raise CommandError(str(error)) from error

        with source:
            size = os.fstat(source.fileno()).st_size
            send_message(self.connection, command="upload", path=remote_path, size=size)
            message = self.reply()
            if message.get("status") == "overwrite":
                overwrite = self.confirm_overwrite(remote_path)
                send_message(self.connection, overwrite=overwrite)
                message = self.reply()
                if not overwrite:
                    self.expect(message, "cancelled")
                    self.report("Upload cancelled.")
                    return False

            self.expect(message, "ready")
            started = perf_counter()
            send_file(self.connection, source, size)
            self.expect(self.reply(), "ok")
            self.transfer = perf_counter() - started
            self.size = size
        self.print_transfer("Uploaded", remote_path)
        return True

    def download(self, remote_path, destination):
        if destination.exists():
            raise CommandError(f"Local path already exists: {destination}. Choose another name.")

        try:
            output = NamedTemporaryFile(dir=destination.parent, prefix=".download-", delete=False)
        except OSError as error:
            raise CommandError(f"Cannot open local destination: {error}") from error

        temporary = Path(output.name)
        try:
            with output:
                send_message(self.connection, command="download", path=remote_path)
                message = self.reply()
                self.expect(message, "ok")
                size = file_size(message)
                started = perf_counter()
                receive_file(self.reader, output, size)
            temporary.replace(destination)
            self.transfer = perf_counter() - started
            self.size = size
            send_message(self.connection, status="ok")
            self.expect(self.reply(), "ok")
        finally:
            temporary.unlink(missing_ok=True)
        self.print_transfer("Downloaded", destination)

    def print_transfer(self, verb, path):
        rate = self.size / 1_000_000 / self.transfer if self.transfer else 0
        self.report(f"{verb} {path} ({self.size:,} bytes, {self.transfer:.3f} s, {rate:.2f} MB/s)")


class FileWindow:
    def __init__(self, window, client):
        self.window = window
        self.client = client
        self.client.report = self.record_message
        self.client.confirm_overwrite = self.ask_overwrite
        self.events = Queue()
        self.messages = []
        self.entries = {}
        self.folder = ""
        self.busy = False
        self.closing = False
        self.host = tk.StringVar()
        self.port = tk.StringVar(value="5050")
        self.connection_label = tk.StringVar(value="Disconnected")
        self.path_label = tk.StringVar(value="/")
        self.status = tk.StringVar(value="Enter the server IP address to connect.")

        window.title("CNT3004 File Sharing")
        window.geometry("760x490")
        window.minsize(630, 380)
        window.protocol("WM_DELETE_WINDOW", self.close)

        panel = ttk.Frame(window, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        connection = ttk.LabelFrame(panel, text="Server", padding=10)
        connection.grid(row=0, column=0, sticky="ew")
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="IP address").grid(row=0, column=0, padx=(0, 8))
        self.host_entry = ttk.Entry(connection, textvariable=self.host)
        self.host_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(connection, text="Port").grid(row=0, column=2, padx=(12, 8))
        self.port_entry = ttk.Entry(connection, textvariable=self.port, width=7)
        self.port_entry.grid(row=0, column=3)
        self.connect_button = ttk.Button(connection, text="Connect", command=self.connect)
        self.connect_button.grid(row=0, column=4, padx=(8, 0))
        ttk.Label(connection, textvariable=self.connection_label).grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(8, 0)
        )
        for entry in (self.host_entry, self.port_entry):
            entry.bind("<Return>", self.connect)
        self.host_entry.focus_set()

        navigation = ttk.Frame(panel)
        navigation.grid(row=1, column=0, sticky="ew", pady=(14, 8))
        ttk.Label(navigation, text="Shared folder").pack(side="left", padx=(0, 8))
        ttk.Entry(navigation, textvariable=self.path_label, state="readonly").pack(
            side="left", fill="x", expand=True
        )
        self.up_button = ttk.Button(navigation, text="Up", command=self.go_up)
        self.up_button.pack(side="left", padx=(8, 0))
        self.refresh_button = ttk.Button(navigation, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(8, 0))

        actions = ttk.Frame(panel)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.upload_button = ttk.Button(actions, text="Upload...", command=self.upload)
        self.folder_button = ttk.Button(actions, text="New Folder...", command=self.make_folder)
        self.open_button = ttk.Button(actions, text="Open Folder", command=self.open_selection)
        self.download_button = ttk.Button(actions, text="Download...", command=self.download)
        self.delete_button = ttk.Button(actions, text="Delete...", command=self.delete)
        for button in (self.upload_button, self.folder_button, self.open_button,
                       self.download_button, self.delete_button):
            button.pack(side="left", padx=(0, 6))

        listing = ttk.Frame(panel)
        listing.grid(row=3, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(listing, columns=("name", "kind"), show="headings",
                                 selectmode="browse")
        self.tree.heading("name", text="Name", anchor="w")
        self.tree.heading("kind", text="Type", anchor="w")
        self.tree.column("name", minwidth=180)
        self.tree.column("kind", width=110, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.update_controls())
        self.tree.bind("<Return>", self.open_selection)
        self.tree.bind("<Double-1>", self.open_selection)

        self.progress = ttk.Progressbar(panel, mode="determinate")
        self.progress.grid(row=4, column=0, sticky="ew", pady=(10, 6))
        self.feedback = ttk.Label(panel, textvariable=self.status, anchor="w")
        self.feedback.grid(row=5, column=0, sticky="ew")
        panel.bind("<Configure>", lambda event: self.feedback.configure(
            wraplength=max(100, event.width - 36)
        ))
        self.update_controls()

    def run(self):
        self.window.after(40, self.poll_events)
        self.window.mainloop()

    def selected(self):
        selection = self.tree.selection()
        return self.entries.get(selection[0]) if selection else None

    def update_controls(self):
        available = not self.busy and not self.closing
        connected = available and self.client.connection is not None
        selected = self.selected()
        for widget, enabled in (
            (self.host_entry, available), (self.port_entry, available),
            (self.connect_button, available), (self.refresh_button, connected),
            (self.up_button, connected and bool(self.folder)),
            (self.upload_button, connected), (self.folder_button, connected),
            (self.open_button, connected and selected and selected[1]),
            (self.download_button, connected and selected and not selected[1]),
            (self.delete_button, connected and selected),
        ):
            widget.state(["!disabled" if enabled else "disabled"])

    def record_message(self, text):
        self.messages.append(text)

    def ask_overwrite(self, path):
        # All Tk calls stay on the main thread. The socket worker waits for the answer.
        answer = Queue(maxsize=1)
        self.events.put(("overwrite", path, answer))
        return answer.get()

    def submit(self, command, args, status, *, folder=None):
        if self.busy or self.closing:
            return
        self.busy = True
        self.status.set(status)
        self.progress.configure(mode="indeterminate")
        self.progress.start(14)
        self.update_controls()
        Thread(target=self.run_command, args=(command, args, folder), daemon=True).start()

    def run_command(self, command, args, folder):
        self.messages = []
        entries = None
        try:
            self.client.execute(command, args)
            if self.client.outcome == "ok" and folder is not None:
                if command != "dir":
                    self.client.execute("dir", [folder] if folder else [])
                entries = self.client.entries
        except (OSError, ValueError) as error:
            self.record_message(f"Error: {error}")
        finally:
            self.events.put(("done", folder, entries, self.messages.copy()))

    def poll_events(self):
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "overwrite":
                    path, answer = data
                    answer.put(not self.closing and messagebox.askyesno(
                        "Replace server file?", f"Replace /{path}?", parent=self.window
                    ))
                elif self.closing:
                    self.window.destroy()
                    return
                else:
                    self.complete(*data)
        except Empty:
            pass
        self.window.after(40, self.poll_events)

    def complete(self, folder, entries, messages):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        if self.client.connection is None:
            self.connection_label.set("Disconnected")
            self.show_folder("", [])
        else:
            self.connection_label.set(f"Connected to {self.client.peer}")
            if entries is not None:
                self.show_folder(folder, entries)
            elif messages and messages[0].startswith("Connected to "):
                self.show_folder("", [])
        self.status.set("   ".join(messages) if messages else
                        f"{len(self.entries)} item(s) in /{self.folder}")
        self.update_controls()

    def show_folder(self, folder, entries):
        self.folder = folder
        self.path_label.set(f"/{folder}")
        self.entries.clear()
        self.tree.delete(*self.tree.get_children())
        for name, is_directory in sorted(entries, key=lambda item: (not item[1], item[0].casefold())):
            row = self.tree.insert("", "end", values=(name, "Folder" if is_directory else "File"))
            self.entries[row] = (name, is_directory)

    def connect(self, event=None):
        host = self.host.get().strip()
        if host:
            self.submit("connect", [host, self.port.get().strip() or "5050"],
                        f"Connecting to {host}...", folder="")
        else:
            self.status.set("Enter the server IP address.")

    def browse(self, folder):
        self.submit("dir", [folder] if folder else [], f"Loading /{folder}...", folder=folder)

    def refresh(self):
        self.browse(self.folder)

    def go_up(self):
        self.browse(self.folder.rpartition("/")[0])

    def remote_path(self, name):
        return f"{self.folder}/{name}" if self.folder else name

    def open_selection(self, event=None):
        if event is not None and event.type == tk.EventType.ButtonPress:
            entry = self.entries.get(self.tree.identify_row(event.y))
        else:
            entry = self.selected()
        if entry and entry[1]:
            self.browse(self.remote_path(entry[0]))

    def upload(self):
        filename = filedialog.askopenfilename(title="Select file to upload", parent=self.window)
        if filename:
            name = Path(filename).name
            self.submit("upload", [filename, self.remote_path(name)],
                        f"Uploading {name}...", folder=self.folder)

    def download(self):
        entry = self.selected()
        if entry and not entry[1]:
            name = entry[0]
            filename = filedialog.asksaveasfilename(
                title="Save downloaded file as", initialfile=name, parent=self.window
            )
            if filename:
                self.submit("download", [self.remote_path(name), filename], f"Downloading {name}...")

    def make_folder(self):
        name = simpledialog.askstring("New Folder", "Folder name", parent=self.window)
        if name and name.strip():
            name = name.strip()
            self.submit("subfolder", ["create", self.remote_path(name)],
                        f"Creating {name}...", folder=self.folder)

    def delete(self):
        entry = self.selected()
        if entry:
            name, is_directory = entry
            path = self.remote_path(name)
            noun = "empty folder" if is_directory else "file"
            if messagebox.askyesno("Confirm deletion", f"Delete {noun} /{path}?",
                                   parent=self.window):
                command = "subfolder" if is_directory else "delete"
                args = ["delete", path] if is_directory else [path]
                self.submit(command, args, f"Deleting {name}...", folder=self.folder)

    def close(self):
        if not self.busy:
            self.window.destroy()
        elif not self.closing and messagebox.askyesno(
            "Operation in progress", "Stop the current operation and close the client?",
            parent=self.window
        ):
            self.closing = True
            self.status.set("Closing...")
            connection = self.client.connection
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=Path("client_metrics.csv"))
    args = parser.parse_args()

    try:
        client = Client(Metrics(args.log, "client"))
        window = tk.Tk()
    except (OSError, tk.TclError) as error:
        parser.exit(1, f"Cannot start the desktop client: {error}\n")

    try:
        FileWindow(window, client).run()
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

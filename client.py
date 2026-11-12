
import json
import os
import queue
import socket
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

CHUNK = 64 * 1024
MAX_HEADER = 64 * 1024


def send_json(sock, obj):
    """Serialize a control object as one newline-terminated JSON message."""
    sock.sendall(json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n")


def recv_json(sock):
    """Read and decode one JSON response, enforcing the maximum header size."""
    data = bytearray()
    while True:
        char = sock.recv(1)
        if not char:
            raise ConnectionError("Server closed the connection")
        if char == b"\n":
            break
        data.extend(char)
        if len(data) > MAX_HEADER:
            raise ValueError("Response header too large")
    return json.loads(data.decode("utf-8"))


def safe_child_name(name):
    """Reject names that could escape the current remote directory."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid file or folder name")
    return name


class FileShareClient(tk.Tk):
    """Tkinter interface for browsing and transferring server files."""

    def __init__(self):
        """Create the window, connection settings, file list, and action controls.

        Network work is tracked through an event queue; polling that queue from
        Tk's main loop keeps UI updates on the thread that owns the widgets.
        """
        super().__init__()
        self.title("File Sharing Client")
        self.geometry("850x560")
        self.minsize(650, 440)
        self.events = queue.Queue()
        self.busy = False
        self.cwd = ""
        self.entries = {}

        self.host = tk.StringVar(value="127.0.0.1")
        self.port = tk.StringVar(value="9000")
        self.location = tk.StringVar(value="/")
        self.status = tk.StringVar(value="Enter a server address and click Connect")

        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Server:").pack(side="left")
        ttk.Entry(top, textvariable=self.host, width=24).pack(side="left", padx=6)
        ttk.Label(top, text="Port:").pack(side="left")
        ttk.Entry(top, textvariable=self.port, width=8).pack(side="left", padx=6)
        self.connect_button = ttk.Button(top, text="Connect / Refresh", command=self.refresh)
        self.connect_button.pack(side="left", padx=6)

        bar = ttk.Frame(self, padding=(12, 0, 12, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="Up", command=self.go_up).pack(side="left")
        ttk.Label(bar, textvariable=self.location).pack(side="left", padx=12)

        listing = ttk.Frame(self, padding=(12, 0, 12, 0))
        listing.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(listing, columns=("type", "size"), show="tree headings")
        self.tree.heading("#0", text="Name")
        self.tree.heading("type", text="Type")
        self.tree.heading("size", text="Size (bytes)")
        self.tree.column("#0", width=440)
        self.tree.column("type", width=120)
        self.tree.column("size", width=130, anchor="e")
        yscroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self.open_selected)

        buttons = ttk.Frame(self, padding=12)
        buttons.pack(fill="x")
        for text, callback in (
            ("Open Folder", self.open_selected),
            ("Upload", self.upload),
            ("Download", self.download),
            ("New Folder", self.mkdir),
            ("Delete", self.delete),
        ):
            ttk.Button(buttons, text=text, command=callback).pack(side="left", padx=(0, 8))

        bottom = ttk.Frame(self, padding=(12, 0, 12, 12))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))
        ttk.Label(bottom, textvariable=self.status).pack(anchor="w")
        self.after(75, self.poll_events)

    def selected(self):
        """Return the metadata for the selected row or raise if none is selected."""
        selection = self.tree.selection()
        if not selection:
            raise ValueError("Select a file or folder first")
        return self.entries[selection[0]]

    def remote_path(self, name):
        """Validate a single child name and join it to the current server folder."""
        safe_child_name(name)
        return "/".join(filter(None, (self.cwd, name)))

    def connection(self):
        """Validate the entered server address and open a timed TCP connection."""
        host = self.host.get().strip()
        if not host:
            raise ValueError("Enter a server address")
        port = int(self.port.get())
        if not (1 <= port <= 65535):
            raise ValueError("Port must be between 1 and 65535")
        return socket.create_connection((host, port), timeout=10)

    def request(self, req, payload=None, destination=None):
        """Run one server request and optionally stream an upload or download.

        Upload bytes are sent in fixed-size chunks. For downloads, the response
        supplies the byte count; data is written to a temporary file and only
        renamed to the destination after all bytes arrive. Progress is posted
        to the UI event queue instead of updating Tk widgets from this method.
        """
        with self.connection() as sock:
            sock.settimeout(30)
            send_json(sock, req)
            if payload is not None:
                total = os.path.getsize(payload)
                sent = 0
                with open(payload, "rb") as stream:
                    while chunk := stream.read(CHUNK):
                        sock.sendall(chunk)
                        sent += len(chunk)
                        self.events.put(("progress", int(sent * 100 / max(total, 1))))
            response = recv_json(sock)
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Server rejected request"))
            if destination is not None:
                length = response.get("size")
                if not isinstance(length, int) or length < 0:
                    raise ValueError("Server returned an invalid file size")
                remaining = length
                # Use an incomplete temporary file so failed downloads don't look finished.
                tmp = Path(str(destination) + ".part")
                try:
                    with open(tmp, "wb") as stream:
                        while remaining:
                            chunk = sock.recv(min(CHUNK, remaining))
                            if not chunk:
                                raise ConnectionError("Download interrupted")
                            stream.write(chunk)
                            remaining -= len(chunk)
                            self.events.put(("progress", int((length - remaining) * 100 / max(length, 1))))
                    os.replace(tmp, destination)
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise
            return response

    def run_job(self, label, operation, on_success=None):
        """Run a network operation on a worker thread and report its result.

        Only one operation may run at a time. The worker places completion or
        failure events on the queue for poll_events to handle in the UI thread.
        """
        if self.busy:
            return
        self.busy = True
        self.progress["value"] = 0
        self.status.set(label)
        def worker():
            """Execute the job off the UI thread and enqueue its outcome."""
            try:
                result = operation()
                self.events.put(("done", label, result, on_success))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_events(self):
        """Apply queued progress/results to widgets and schedule the next poll."""
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.progress["value"] = event[1]
                elif event[0] == "done":
                    _, label, result, callback = event
                    self.busy = False
                    self.progress["value"] = 100
                    self.status.set(label + " — complete")
                    if callback:
                        callback(result)
                else:
                    self.busy = False
                    self.status.set("Error: " + event[1])
                    messagebox.showerror("Operation failed", event[1])
        except queue.Empty:
            pass
        self.after(75, self.poll_events)

    def refresh(self):
        """Request a fresh listing for the folder currently being browsed."""
        target = self.cwd
        self.run_job("Loading files", lambda: self.request({"cmd": "LIST", "path": target}), self.show_listing)

    def show_listing(self, response):
        """Replace the visible file rows with valid entries from a LIST response."""
        self.tree.delete(*self.tree.get_children())
        self.entries.clear()
        for item in response.get("entries", []):
            name = item.get("name", "")
            try:
                safe_child_name(name)
            except ValueError:
                continue
            kind = item.get("type")
            if kind not in ("file", "dir"):
                continue
            size = item.get("size", 0) if kind == "file" else ""
            row = self.tree.insert("", "end", text=name, values=("Folder" if kind == "dir" else "File", size))
            self.entries[row] = item
        self.location.set("/" + self.cwd)

    def open_selected(self, _event=None):
        """Open a selected folder, or start downloading a selected file."""
        try:
            item = self.selected()
            if item["type"] == "dir":
                self.cwd = self.remote_path(item["name"])
                self.refresh()
            else:
                self.download()
        except ValueError as exc:
            messagebox.showinfo("Select an item", str(exc))

    def go_up(self):
        """Navigate to the parent server folder and refresh its contents."""
        if self.busy:
            return
        self.cwd = self.cwd.rpartition("/")[0]
        self.refresh()

    def upload(self):
        """Choose a local file and upload it into the current server folder."""
        path = filedialog.askopenfilename(title="Choose a file to upload")
        if not path:
            return
        remote = self.remote_path(Path(path).name)
        size = os.path.getsize(path)
        self.run_job("Uploading " + Path(path).name,
                     lambda: self.request({"cmd": "UPLOAD", "path": remote, "size": size}, payload=path),
                     lambda _resp: self.refresh())

    def download(self):
        """Choose a destination and download the selected server-side file."""
        try:
            item = self.selected()
            if item["type"] != "file":
                raise ValueError("Select a file to download")
            remote = self.remote_path(item["name"])
        except ValueError as exc:
            messagebox.showinfo("Select a file", str(exc))
            return
        destination = filedialog.asksaveasfilename(title="Save downloaded file", initialfile=item["name"])
        if destination:
            self.run_job("Downloading " + item["name"],
                         lambda: self.request({"cmd": "DOWNLOAD", "path": remote}, destination=destination))

    def mkdir(self):
        """Ask for a folder name, validate it, and create it on the server."""
        from tkinter import simpledialog
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name:
            return
        try:
            remote = self.remote_path(name)
        except ValueError as exc:
            messagebox.showerror("Invalid name", str(exc))
            return
        self.run_job("Creating folder", lambda: self.request({"cmd": "MKDIR", "path": remote}),
                     lambda _resp: self.refresh())

    def delete(self):
        """Confirm and delete the selected server item, then refresh the view.

        The server requires folders to be empty before they can be deleted.
        """
        try:
            item = self.selected()
            remote = self.remote_path(item["name"])
        except ValueError as exc:
            messagebox.showinfo("Select an item", str(exc))
            return
        if not messagebox.askyesno("Confirm delete", f"Delete {item['name']} from the server?\nFolders must be empty."):
            return
        self.run_job("Deleting " + item["name"], lambda: self.request({"cmd": "DELETE", "path": remote}),
                     lambda _resp: self.refresh())


if __name__ == "__main__":
    FileShareClient().mainloop()

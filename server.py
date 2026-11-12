import socket
import threading
from pathlib import Path
import uuid
from protocol import send_message, receive_message
from file_manager import (
    initialize_database,
    save_file_record,
    get_file_record,
    list_files
)
from authentication import authenticate_user

HOST = "0.0.0.0"
PORT = 5050


def process_request(request, user_id):
    action = request.get("action")

    if action == "LIST":
        files = list_files(user_id)

        return {
            "status": "success",
            "files": files
        }
    elif action == "UPLOAD":
        return {
            "status": "error",
            "message": "Upload must use the file transfer handler"
        }
    elif action == "DOWNLOAD":
        return {
            "status": "error",
            "message": "Download must use the file transfer handler"
        }
    else:
        return {
            "status": "error",
            "message": "Unknown action"
        }

STORAGE_DIR = Path(__file__).resolve().parent / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

CHUNK_SIZE = 64 * 1024
MAX_FILE_SIZE = 100 * 1024 * 1024 # 100MB

FILE_TYPES = {
    ".txt": ("text", "txt"),
    ".pdf": ("text", "txt"),
    ".docx": ("text", "txt"),
    ".mp3": ("audio", "aud"),
    ".wav": ("audio", "aud"),
    ".mp4": ("video", "vid"),
    ".mov": ("video", "vid"),
    ".jpg": ("image", "img"),
    ".jpeg": ("image", "img"),
    ".png": ("image", "img"),

}

def get_file_category(filename):
    extension = Path(filename).suffix.lower()
    return FILE_TYPES.get(extension, ("other", "oth"))



def handle_upload(conn, request, user_id):
    filename = request.get("filename")
    filesize = request.get("filesize")

    if not isinstance(filename, str) or not filename:
        return {"status": "error", "message": "Invalid filename"}

    if type(filesize) is not int or not 0 <= filesize <= MAX_FILE_SIZE:
        return{"status": "error", "message": "Invalid file size"}

    #use only the extension; never trust a client-supplied path
    extension = Path(filename).suffix.lower()

    category, prefix = get_file_category(filename)

    category_dir = STORAGE_DIR / category
    category_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex
    stored_name = f"{prefix}_{file_id}{extension}"
    destination = category_dir / stored_name

    #tell the client that the server is ready for file bytes
    send_message(conn, {"status": "ready"})

    remaining = filesize

    try:
        with open(destination, "wb") as file:
            while remaining > 0:
                chunk = conn.recv(min(CHUNK_SIZE, remaining))

                if not chunk:
                    raise ConnectionError("Upload interrupted")

                file.write(chunk)
                remaining -= len(chunk)

    except (OSError, ConnectionError):
        destination.unlink(missing_ok=True)
        raise

    try:
        save_file_record(
            file_id=file_id,
            original_name=filename,
            stored_name=stored_name,
            category=category,
            file_size=filesize,
            owner_id=user_id
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {
        "status": "success",
        "message": "File uploaded successfully",
        "file_id": file_id
    }

def handle_download(conn, request, user_id):
    file_id = request.get("file_id")

    if not isinstance(file_id, str):
        return {
            "stats": "error",
            "message": "Invalid file ID"
        }
    record = get_file_record(file_id)

    if record is None:
        return {
            "status": "error",
            "message": "File not found"
        }
    #temporary ownership check until authentication exists

    if record["owner_id"] != user_id:
        return {
            "status": "error",
            "message": "Access denied"
        }
    category = record["category"]
    stored_name = record["stored_name"]

    #build the path from trusted storage metadata
    filepath = STORAGE_DIR / category / stored_name

    if not filepath.is_file():
        return {
            "status": "error",
            "message": "File missing from storage"

        }
    filesize = filepath.stat().st_size

    #send the file information before the binary data
    send_message(conn, {
        "status": "ready",
        "filename": record["original_name"],
        "filesize": filesize
    })

    #stream the file in chunks
    with open(filepath, "rb") as file:
        while True:
            chunk = file.read(CHUNK_SIZE)

            if not chunk:
                break

            conn.sendall(chunk)
    return None


def handle_client(conn, addr):
    print(f"Client connected: {addr}")

    #each client connection has its own authenticate state
    user_id = None

    try:
        with conn:
            while True:
                try:
                    request = receive_message(conn)

                except ConnectionError:
                    break

                if not isinstance(request, dict):
                    send_message(conn, {
                        "status": "error",
                        "message": "Invalid request"
                    })
                    continue
                action = request.get("action")

                if action == "LOGIN":
                    username = request.get("username")
                    password = request.get("password")

                    authenticated_id = authenticate_user(
                        username, password
                    )

                    if authenticated_id is not None:
                        user_id = authenticated_id

                        response = {
                            "status": "success",
                            "message": "Login successful"
                        }

                    else:
                        user_id = None

                        response = {
                            "status": "error",
                            "message": "Invalid credentials"
                        }

                elif user_id is None:
                    response = {
                        "status": "error",
                        "message": "Authentication required"
                    }
                elif action == "UPLOAD":
                    response = handle_upload(conn, request, user_id)

                elif action == "DOWNLOAD":
                    response = handle_download(conn, request, user_id)

                elif action == "LIST":
                    response = process_request(request, user_id)

                else:
                    response = {
                        "status": "error",
                        "message": "Unknown action"
                    }

                if response is not None:
                    send_message(conn, response)
    except (OSError, ValueError) as error:
        print(f"Client error {addr}: {error}")

    finally:
        print(f"Client disconnected: {addr}")




initialize_database()

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()

    print(f"Server listening on port {PORT}")

    while True:
        conn, addr = server.accept()

        client_thread = threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True
        )
        client_thread.start()

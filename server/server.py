import shutil
import socket
import threading
import traceback
from pathlib import Path
import hashlib
import uuid
import traceback
from protocol import send_message, receive_message
from authentication import authenticate_user
import ssl
from file_manager import (
    initialize_database,
    save_file_record,
    get_file_record,
    list_files,
    create_folder,
    validate_folder,
    delete_folder,
    list_directory,
    acquire_file,
    release_file,
    delete_file,
    find_existing_file,
    acquire_upload,
    release_upload,
    replace_file_record
)

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
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024

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
    folder_id = request.get("folder_id")
    overwrite = request.get("overwrite", False)

    if not isinstance(filename, str) or not filename:
        return {"status": "error", "message": "Invalid filename"}

    if type(filesize) is not int or not 0 <= filesize <= MAX_FILE_SIZE:
        return{"status": "error", "message": "Invalid file size"}

    if type(overwrite) is not bool:
        return {"status": "error", "message": "Invalid overwrite option"}

    #validate destination folder
    if not validate_folder(user_id, folder_id):
        return {
            "status": "error",
            "message": "Destination folder not found or access denied"
        }
    #prevent simultaneous uploads with the same filename
    if not acquire_upload(user_id, folder_id, filename):
        return {
            "status": "error",
            "message": "An Upload with this filename already in progress"
        }

    locked_file_id = None

    try:
        #check for an existing file while holding the lock
        existing_file = find_existing_file(
            owner_id=user_id,
            filename=filename,
            folder_id=folder_id
        )

        if existing_file is not None:
            if not overwrite:
                return {
                    "status": "overwrite_required",
                    "message": "A file with this name already exists",
                    "file_id": existing_file["file_id"],
                    "filename": existing_file["original_name"]
                }

            #protect the original file during replacement
            file_id = existing_file["file_id"]

            if not acquire_file(file_id):
                return {
                    "status": "error",
                    "message": "File is currently being processed"
                }

            locked_file_id = file_id

            #verify the original has not changed or been deleted
            current_record = get_file_record(file_id)

            if (current_record is None
                    or current_record["owner_id"] != user_id
                    or current_record["original_name"] != filename
                    or current_record["folder_id"] != folder_id):
                return {
                    "status": "error",
                    "message": "Original file changed; retry upload"
                }
            old_path = (
                STORAGE_DIR
                / current_record["category"]
                / current_record["stored_name"]
            )

            if not old_path.is_file():
                return {
                    "status": "error",
                    "message": "Original file missing from storage"
                }
        else:
            if overwrite:
                return {
                    "status": "error",
                    "message": "No existing file to overwrite"
                }
            file_id = uuid.uuid4().hex
            old_path = None

        extension = Path(filename).suffix.lower()
        category, prefix = get_file_category(filename)

        category_dir = STORAGE_DIR / category
        category_dir.mkdir(parents=True, exist_ok=True)

        #always generate a new physical storage filename
        new_storage_id = uuid.uuid4().hex
        stored_name = f"{prefix}_{new_storage_id}{extension}"
        destination = category_dir / stored_name

        try:
            send_message(conn, {"status": "ready"})
            remaining = filesize

            with open(destination, "wb") as file:
                while remaining > 0:
                    chunk = conn.recv(min(CHUNK_SIZE, remaining))

                    if not chunk:
                        raise ConnectionError("Upload interrupted")

                    file.write(chunk)
                    remaining -= len(chunk)

            if old_path is not None:
                #update the existing SQlite record
                replace_file_record(
                    owner_id=user_id,
                    file_id=file_id,
                    stored_name=stored_name,
                    category=category,
                    file_size=filesize
                )
            else:
                #insert a new SQlite record
                save_file_record(
                    file_id=file_id,
                    original_name=filename,
                    stored_name=stored_name,
                    category=category,
                    file_size=filesize,
                    owner_id=user_id,
                    folder_id=folder_id
                )
        except Exception:
            #preserve the original if replacement fails
            destination.unlink(missing_ok=True)
            raise

        #the database points to the replacement
        if old_path is not None:
            try:
                old_path.unlink(missing_ok=True)
            except OSError as error:
                print(f"Old file cleanup failed: {error}")

        return {
            "status": "success",
            "message": (
                "File overwritten successfully"
                if old_path is not None
                else "File uploaded successfully"
            ),
            "file_id": file_id
        }
    finally:
        if locked_file_id is not None:
            release_file(locked_file_id)

        release_upload(user_id, folder_id, filename)




def handle_download(conn, request, user_id):
    file_id = request.get("file_id")
    #Byte position from which the download should resume
    offset = request.get("offset", 0)

    #reject invalid offset
    #bool is excluded because bool is an int in python
    if type(offset) is not int or offset < 0:
        return {
            "status": "error",
            "message": "Invalid download offset"

    }

    #validate the file ID
    if not isinstance(file_id, str) or not file_id:
        return {
            "status": "error",
            "message": "Invalid file ID"
        }

    if not acquire_file(file_id):
        return {
            "status": "error",
            "message": "File is currently being processed"
        }
    try:
        #retrieve the file record while holding the lock
        record = get_file_record(file_id)

        if record is None:
            return {
                "status": "error",
                "message": "File not found"
            }
        #verify that the authenticated user owns the file.

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
        #validate that the offset is within the file
        if offset > filesize:
            return {
                "status": "error",
                "message": "Offset exceeds file size"
            }

        #Calculate SHA-256 without loading the whole file into memory
        sha256 = hashlib.sha256()

        with open(filepath, "rb") as file:
            while True:
                chunk = file.read(CHUNK_SIZE)

                if not chunk:
                    break

                sha256.update(chunk)

        file_hash = sha256.hexdigest()

        #send download metadata to the client
        send_message(conn, {
            "status": "ready",
            "filename": record["original_name"],
            "filesize": filesize,
            "offset": offset,
            "sha256": file_hash
        })

        #send file data begining at the requested offset
        with open(filepath, "rb") as file:
            file.seek(offset)

            while True:
                chunk = file.read(CHUNK_SIZE)

                if not chunk:
                    break

                conn.sendall(chunk)

        return None
    finally:
        release_file(file_id)

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

                elif action == "SUBFOLDER":
                    operation = request.get("operation")

                    if operation == "create":
                        response = create_folder(
                            owner_id=user_id,
                            folder_name=request.get("folder_name"),
                            parent_folder_id=request.get("parent_folder_id")
                        )

                    elif operation == "delete":
                        response = delete_folder(
                            owner_id=user_id,
                            folder_id=request.get("folder_id")
                        )

                    else:
                        response = {
                            "status": "error",
                            "message": "Unsupported subfolder operation"
                        }
                elif action == "DIR":
                    response = list_directory(
                        owner_id=user_id,
                        folder_id=request.get("folder_id")
                    )

                elif action == "DELETE":
                    response = delete_file(
                        owner_id=user_id,
                        file_id=request.get("file_id")
                    )

                else:
                    response = {
                        "status": "error",
                        "message": "Unknown action"
                    }

                if response is not None:
                    send_message(conn, response)

    except (OSError, ValueError) as error:
        print(f"Client error {addr}: {error}")

    except Exception as error:
        print(f"Unexpected error from client {addr}: {error}")
        traceback.print_exc()

    finally:
        print(f"Client disconnected: {addr}")


def handle_secure_client(conn, addr, tls_context):
    try:
        #limit how long a client can take to complete TLS
        conn.settimeout(10)

        with tls_context.wrap_socket(
            conn,
            server_side=True
        ) as secure_conn:

            #restore blocking mode for normal file transfers
            secure_conn.settimeout(None)

            handle_client(secure_conn, addr)

    except (ssl.SSLError, socket.timeout, OSError) as error:
        print(f"Connection error for {addr}: {error}")

    finally:
        conn.close()


BASE_DIR = Path(__file__).resolve().parent

tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

tls_context.minimum_version = ssl.TLSVersion.TLSv1_2

tls_context.load_cert_chain(
    certfile=BASE_DIR / "server.crt",
    keyfile=BASE_DIR / "server.key"
)

initialize_database()

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()

    print(f"Secure TLS server listening on port {PORT}")

    while True:
        conn, addr = server.accept()

        client_thread = threading.Thread(
            target=handle_secure_client,
            args=(conn, addr, tls_context),
            daemon=True
        )
        client_thread.start()
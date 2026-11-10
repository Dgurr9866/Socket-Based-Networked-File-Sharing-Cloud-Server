import socket
from fileinput import filename
from pathlib import Path

from protocol import send_message, receive_message

HOST = "127.0.0.1"
PORT = 5050

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

file_id = input("Enter the file ID to download: ").strip()

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
    client.connect((HOST, PORT))

    request = {
        "action": "DOWNLOAD",
        "file_id": file_id
    }

    send_message(client, request)

    response = receive_message(client)
    print("Server response: ", response)

    if response.get("status") == "ready":
        filename = Path(response["filename"]).name
        filesize = response["filesize"]

        if (not filename or filename in (".", "..")
                or type(filesize) is not int or filesize < 0):
            raise ValueError("Invalid download metadata")

        destination = DOWNLOAD_DIR / filename
        remaining = filesize

        try:
            with open(destination, "wb") as file:
                while remaining > 0:
                    chunk = client.recv(min(65536, remaining))

                    if not chunk:
                        raise ConnectionError("Download interrupted")

                    file.write(chunk)
                    remaining -= len(chunk)

        except (OSError, ConnectionError):
            destination.unlink(missing_ok=True)
            raise

        print(f"Download complete: {destination}")





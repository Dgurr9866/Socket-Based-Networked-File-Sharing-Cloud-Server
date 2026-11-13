import json
import struct

MAX_MESSAGE_SIZE = 1024 * 1024 #1 MB
CMD_METRICS = "METRICS"
CMD_UPLOAD = "UPLOAD"
CMD_DOWNLOAD = "DOWNLOAD"
CMD_LIST = "LIST"

def receive_exact(sock, size):
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError("Connection closed unexpectedly")

        data.extend(chunk)

    return bytes(data)

def send_message(sock, message):
    data = json.dumps(message).encode("utf-8")

    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("Message is too large")

    header = struct.pack("!I", len(data))

    sock.sendall(header + data)


def receive_message(sock):
    header = receive_exact(sock, 4)

    message_length = struct.unpack("!I", header)[0]

    if message_length > MAX_MESSAGE_SIZE:
        raise ValueError("Message is too large")

    data = receive_exact(sock, message_length)

    return json.loads(data.decode("utf-8"))


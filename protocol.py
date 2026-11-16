"""TCP messages are newline-delimited JSON followed by any declared file bytes."""

import json

CHUNK_SIZE = 1024 * 1024
MAX_HEADER = 64 * 1024


class ProtocolError(Exception):
    """The TCP stream cannot be used for another request."""


def send_message(connection, **fields):
    """Send a newline-delimited JSON message over a socket connection."""
    connection.sendall(json.dumps(fields).encode("utf-8") + b"\n")


def read_message(reader):
    """Read and validate one newline-delimited JSON object from a stream."""
    line = reader.readline(MAX_HEADER + 1)
    if not line:
        raise EOFError("Peer closed the connection.")
    if len(line) > MAX_HEADER or not line.endswith(b"\n"):
        raise ProtocolError("Invalid message header length.")
    try:
        message = json.loads(line)
    except (ValueError, UnicodeError) as error:
        raise ProtocolError("Invalid JSON header.") from error
    if not isinstance(message, dict):
        raise ProtocolError("Expected a JSON object.")
    return message


def file_size(message):
    """Return a validated nonnegative file size from a protocol message."""
    size = message.get("size")
    if type(size) is not int or size < 0:
        raise ProtocolError("Invalid file size.")
    return size


def send_file(connection, source, remaining):
    """Send exactly the requested number of bytes from an open source file."""
    while remaining:
        chunk = source.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise ProtocolError("The source file became shorter during transfer.")
        connection.sendall(chunk)
        remaining -= len(chunk)


def receive_file(reader, destination, remaining):
    """Write exactly the requested number of bytes from a stream to a file."""
    while remaining:
        chunk = reader.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise EOFError("Connection closed during file transfer.")
        destination.write(chunk)
        remaining -= len(chunk)

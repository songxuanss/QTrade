"""Probe the runner over its private Unix socket."""

import socket
import sys

SOCKET = "/run/qtrade/runner.sock"


def main() -> int:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(SOCKET)
            connection.sendall(b"GET /health HTTP/1.1\r\nHost: runner\r\nConnection: close\r\n\r\n")
            return 0 if connection.recv(1024).startswith(b"HTTP/1.1 200") else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())

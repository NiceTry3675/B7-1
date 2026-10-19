"""Prevent multiple adapter processes from admitting jobs to one runtime."""

import hashlib
import socket


class RuntimeLease:
    def __init__(self, runtime_url):
        digest = hashlib.sha256(runtime_url.encode()).hexdigest()[:24]
        self.name = ("\0b7-rpi-llm-" + digest).encode()
        self.socket = None

    def __enter__(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.socket.bind(self.name)
        except OSError as exc:
            self.socket.close()
            self.socket = None
            raise RuntimeError("another adapter already owns this llama runtime") from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.socket.close()
        self.socket = None

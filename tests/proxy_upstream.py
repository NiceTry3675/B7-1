"""HTTP/SSE/WebSocket upstream used only by the Docker proxy integration test."""

import base64
import hashlib
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/ws":
            key = self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header(
                "Sec-WebSocket-Accept",
                base64.b64encode(hashlib.sha1(key.encode()).digest()).decode(),
            )
            self.end_headers()
            self.wfile.write(b"\x81\x02ok")
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path == "/gradio_api/queue/data":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"data: first\n\n")
            self.wfile.flush()
            time.sleep(3)
            self.wfile.write(b"data: last\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        data = json.dumps(
            {
                "service": sys.argv[1],
                "path": self.path,
                "method": self.command,
                "body": body,
                "headers": dict(self.headers),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_POST = do_GET

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[2])), Handler).serve_forever()

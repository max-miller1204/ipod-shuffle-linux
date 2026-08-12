#!/usr/bin/env python3
"""A camera pointed at a broadway display: one browser, photographed on demand.

The window under test runs under gtk4-broadwayd, which serves it over HTTP to
whatever browser is looking. A browser started fresh for each photograph gives
the frame it was holding before it connected, so the pictures come out one or
two events behind what the window is showing. This keeps a single headless
browser connected for the whole run and asks it, over the DevTools protocol,
for a picture of what it has on screen right now.

Nothing here is part of the product; it is the tripod the evidence is taken
from. The DevTools protocol is spoken over a websocket, and the handshake and
framing are written out here because the machine this was produced on has no
websocket library for the system Python.
"""

import base64
import json
import os
import socket
import subprocess
import time
import urllib.request


def _read_exactly(sock, count):
    chunks = []
    while count:
        chunk = sock.recv(count)
        if not chunk:
            raise ConnectionError("the browser closed the connection")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


class Browser:
    """One headless browser, looking at a page, answering for pictures of it."""

    def __init__(self, url, port, profile, size=(1280, 900)):
        self.process = subprocess.Popen(
            [
                "google-chrome",
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                f"--user-data-dir={profile}",
                f"--window-size={size[0]},{size[1]}",
                f"--remote-debugging-port={port}",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.port = port
        self.next_id = 0
        self.socket = self._connect(url)

    def _target(self, url):
        """The browser's page, once it has one open on the display."""
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=2
                ) as answer:
                    for target in json.load(answer):
                        if target.get("type") == "page" and url in target.get(
                            "url", ""
                        ):
                            return target["webSocketDebuggerUrl"]
            except OSError:
                pass
            time.sleep(0.3)
        raise TimeoutError("the browser never opened the page")

    def _connect(self, url):
        endpoint = self._target(url)
        _, _, rest = endpoint.partition("://")
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET /{path} HTTP/1.1\r\n"
                f"Host: {hostport}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        header = b""
        while b"\r\n\r\n" not in header:
            header += sock.recv(1)
        if b"101" not in header.split(b"\r\n")[0]:
            raise ConnectionError(f"the browser refused the websocket: {header!r}")
        return sock

    def _send(self, payload):
        data = json.dumps(payload).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        header += mask
        self.socket.sendall(
            bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        )

    def _receive(self):
        """One whole message, continuation frames and all."""
        message = b""
        while True:
            first, second = _read_exactly(self.socket, 2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(_read_exactly(self.socket, 2), "big")
            elif length == 127:
                length = int.from_bytes(_read_exactly(self.socket, 8), "big")
            mask = _read_exactly(self.socket, 4) if second & 0x80 else b""
            payload = _read_exactly(self.socket, length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping
                self.socket.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if first & 0x80:
                    return message
            elif opcode == 0x8:
                raise ConnectionError("the browser hung up")

    def photograph(self, path):
        """What the browser has on screen, saved as a PNG."""
        self.next_id += 1
        wanted = self.next_id
        self._send(
            {
                "id": wanted,
                "method": "Page.captureScreenshot",
                "params": {"format": "png"},
            }
        )
        while True:
            answer = json.loads(self._receive())
            if answer.get("id") != wanted:
                continue
            if "error" in answer:
                raise RuntimeError(answer["error"])
            with open(path, "wb") as out:
                out.write(base64.b64decode(answer["result"]["data"]))
            return path

    def close(self):
        try:
            self.socket.close()
        finally:
            self.process.terminate()
            self.process.wait(timeout=10)

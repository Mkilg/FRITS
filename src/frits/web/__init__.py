"""The FRITS web app: `frits ui` starts it on this computer and opens the browser."""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

DEFAULT_PORT = 8765


def free_port(start: int = DEFAULT_PORT) -> int:
    for port in range(start, start + 50):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Kein freier Port gefunden.")


def running_frits(port: int = DEFAULT_PORT) -> bool:
    """Is a FRITS already answering on this port? (Then we just open the browser again.)"""
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.read() == b"FRITS"
    except (URLError, OSError):
        return False


def serve(port: int | None = None, open_browser: bool = True) -> None:
    import uvicorn

    from frits.web.app import create_app

    if port is None and running_frits():
        print(f"FRITS läuft schon: http://127.0.0.1:{DEFAULT_PORT}/")
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/")
        return
    port = port or free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"FRITS läuft: {url}  (Strg+C beendet FRITS)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    # 127.0.0.1 only: nobody else in the Wi-Fi can reach FRITS.
    # (Docker sets FRITS_HOST=0.0.0.0 inside the container and maps the port to 127.0.0.1.)
    host = os.environ.get("FRITS_HOST", "127.0.0.1")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")

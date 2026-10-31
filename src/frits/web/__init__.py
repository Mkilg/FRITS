"""The FRITS web app: `frits ui` starts it on this computer and opens the browser."""

from __future__ import annotations

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


def serve(port: int | None = None, open_browser: bool = True) -> None:
    import uvicorn

    from frits.web.app import create_app

    port = port or free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"FRITS läuft: {url}  (Strg+C beendet FRITS)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    # 127.0.0.1 only: nobody else in the Wi-Fi can reach FRITS.
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")

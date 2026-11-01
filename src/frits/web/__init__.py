"""The FRITS web app: `frits ui` starts it on this computer and opens the browser."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765
RUN_FILE = ".frits-laeuft.json"
"""Written into the FRITS folder while FRITS runs: which version, from where, which process."""


def free_port(start: int = DEFAULT_PORT) -> int:
    for port in range(start, start + 50):
        if _port_free(port):
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


def _run_file() -> Path:
    from frits.settings import Workspace

    return Workspace.default().root / RUN_FILE


def _me() -> dict:
    """This FRITS: same version from the same folder means "the same app, started twice"."""
    import frits

    return {
        "pid": os.getpid(),
        "version": frits.__version__,
        "code": str(Path(frits.__file__).resolve().parent),
    }


def _other() -> dict:
    try:
        return json.loads(_run_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _is_frits(pid: int) -> bool:
    """Only ever stop a process that is FRITS (its command line says so)."""
    if pid == os.getpid():
        return False
    try:
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "frits" in command.lower()


def _listening_pids(port: int) -> list[int]:
    """Processes listening on the port (older FRITS versions leave no run file)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        # Like uvicorn: connections the old FRITS just closed (TIME_WAIT) don't count.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def stop_other_frits(port: int = DEFAULT_PORT, wait: float = 8.0) -> bool:
    """End the FRITS that holds the port (an older version, or a copy from another folder).

    Asks politely first (SIGTERM: FRITS finishes what it is writing), then insists after a
    few seconds. True when the port is free afterwards.
    """
    pids = {int(_other().get("pid") or 0), *_listening_pids(port)} - {0}
    pids = {pid for pid in pids if _is_frits(pid)}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.monotonic() + (wait if sig == signal.SIGTERM else 3)
        while time.monotonic() < deadline:
            if _port_free(port):
                return True
            time.sleep(0.2)
    return _port_free(port)


def _tell(message: str) -> None:
    """Say it in the terminal, and in a Mac dialog when FRITS was started as an app."""
    print(message)
    if os.environ.get("FRITS_APP") == "1" and sys.platform == "darwin":
        script = f'display alert "FRITS" message {json.dumps(message)} as warning'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=600, check=False)


def serve(port: int | None = None, open_browser: bool = True) -> None:
    import uvicorn

    from frits.web.app import create_app

    if port is None and running_frits():
        other, me = _other(), _me()
        if (other.get("version"), other.get("code")) == (me["version"], me["code"]):
            # The same FRITS, double-clicked again: just show it.
            print(f"FRITS läuft schon: http://127.0.0.1:{DEFAULT_PORT}/")
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/")
            return
        # Another FRITS holds the place (an older version, perhaps already in the bin): the
        # one started last is the one Fritz wants.
        version = other.get("version") or "eine ältere Version"
        print(f"Es läuft noch FRITS ({version}). Ich beende es und starte {me['version']}.")
        if not stop_other_frits(DEFAULT_PORT):
            _tell(
                "Es läuft noch eine andere FRITS-Version, und sie lässt sich nicht beenden. "
                "Bitte den Mac einmal neu starten und FRITS dann wieder öffnen."
            )
            return
    port = port or free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"FRITS läuft: {url}  (Strg+C beendet FRITS)")
    app = create_app()
    if open_browser:
        threading.Timer(2.0, _open_unless_waiting, args=(app, url)).start()
    run_file = _run_file() if port == DEFAULT_PORT else None
    if run_file:
        try:
            run_file.write_text(json.dumps(_me()), encoding="utf-8")
        except OSError:
            run_file = None
    # 127.0.0.1 only: nobody else in the Wi-Fi can reach FRITS.
    # (Docker sets FRITS_HOST=0.0.0.0 inside the container and maps the port to 127.0.0.1.)
    host = os.environ.get("FRITS_HOST", "127.0.0.1")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if run_file and _other().get("pid") == os.getpid():
            run_file.unlink(missing_ok=True)


def _open_unless_waiting(app, url: str) -> None:
    """Open FRITS in the browser, unless the "FRITS ist aus" tab already came back by itself."""
    if not app.state.tab_waiting:
        webbrowser.open(url)

"""A tiny local web server for browser/dist/, like GitHub Pages: plain files, no special headers.

    uv run python browser/build.py --serve

`overlay` (for tests) is a folder whose files win over dist/, e.g. a fake helper worker.
"""

from __future__ import annotations

import functools
import http.server
from pathlib import Path
from typing import ClassVar

TYPES = {".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm",
         ".json": "application/json", ".whl": "application/zip", ".webp": "image/webp",
         ".svg": "image/svg+xml", ".gz": "application/gzip"}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map: ClassVar = {**http.server.SimpleHTTPRequestHandler.extensions_map, **TYPES}
    overlay: Path | None = None
    prefix = ""

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if self.prefix and clean.startswith(self.prefix):
            clean = clean[len(self.prefix):] or "/"
        if self.overlay is not None:
            candidate = self.overlay / clean.lstrip("/")
            if candidate.is_file():
                return str(candidate)
        return super().translate_path(clean)

    def log_message(self, *args) -> None:  # quiet
        pass


def serve(root: Path, port: int, overlay: Path | None = None, prefix: str = "") -> None:
    handler = functools.partial(Handler, directory=str(root))
    Handler.overlay = overlay
    Handler.prefix = prefix
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"FRITS im Browser: http://localhost:{port}{prefix}/  (Strg+C beendet)")
        server.serve_forever()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--overlay", type=Path)
    p.add_argument("--prefix", default="", help='serve under a sub-path, e.g. "/FRITS" like Pages')
    a = p.parse_args()
    serve(Path(__file__).parent / "dist", a.port, a.overlay, a.prefix)

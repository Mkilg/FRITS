"""Answer one HTTP-like request with an ASGI app, without a server.

The page's service worker catches every request of the app frame and hands it over as plain
values; we run the FastAPI app on it and hand back status, headers and body. Pure Python, so it
is tested on a normal computer too.

The app frame does not live at the root of the web address (on GitHub Pages it is
`/FRITS/app/`), but FRITS writes its links as `/invoices`. So the prefix is taken off on the way
in and put back in front of every link on the way out.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# A quote or "url(" directly before "/" and then a path: "/invoices", '/steuer/…', url(/static/…).
# "//" (protocol-relative or comments) and "</" (closing tags) are left alone.
_LINK = re.compile(r"""(?P<lead>["']|url\()/(?=[A-Za-z0-9_?#.-]|["')])""")
_TEXT_TYPES = ("text/html", "text/css")


def strip_prefix(path: str, prefix: str) -> str:
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        path = path[len(prefix) :]
    return path or "/"


def add_prefix(text: str, prefix: str) -> str:
    """Put the app's address prefix in front of every root-relative link in HTML or CSS."""
    if not prefix:
        return text

    def fix(m: re.Match[str]) -> str:
        rest = text[m.end() : m.end() + len(prefix)]
        if ("/" + rest).startswith(prefix):  # already prefixed (e.g. location.pathname sent back)
            return m.group(0)
        return f"{m.group('lead')}{prefix}/"

    return _LINK.sub(fix, text)


def _location(value: str, prefix: str) -> str:
    already = value == prefix or value.startswith(prefix + "/")
    if prefix and value.startswith("/") and not value.startswith("//") and not already:
        return prefix + value
    return value


async def handle(
    app,
    method: str,
    url: str,
    headers: list[tuple[str, str]],
    body: bytes,
    prefix: str = "",
) -> dict:
    parts = urlsplit(url)
    path = strip_prefix(parts.path or "/", prefix)
    # Nobody else can reach this app (it runs inside this browser tab), so it is "localhost".
    headers = [(k, v) for k, v in headers if k.lower() != "host"] + [("host", "localhost")]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": parts.query.encode(),
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 0),
        "server": ("localhost", 80),
        "state": {},
    }
    sent_body = False

    async def receive():
        nonlocal sent_body
        if sent_body:
            return {"type": "http.disconnect"}
        sent_body = True
        return {"type": "http.request", "body": body, "more_body": False}

    status = 500
    out_headers: list[tuple[str, str]] = []
    chunks = bytearray()

    async def send(message):
        nonlocal status, out_headers
        if message["type"] == "http.response.start":
            status = message["status"]
            out_headers = [
                (k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", [])
            ]
        elif message["type"] == "http.response.body":
            chunks.extend(message.get("body", b""))

    await app(scope, receive, send)

    data = bytes(chunks)
    content_type = next((v for k, v in out_headers if k.lower() == "content-type"), "")
    if prefix and content_type.startswith(_TEXT_TYPES):
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";")[0].strip()
        data = add_prefix(data.decode(charset, "replace"), prefix).encode(charset)
    fixed = []
    for k, v in out_headers:
        low = k.lower()
        if low == "content-length":
            continue
        if low == "location":
            v = _location(v, prefix)
        fixed.append((k, v))
    fixed.append(("content-length", str(len(data))))
    return {"status": status, "headers": fixed, "body": data}

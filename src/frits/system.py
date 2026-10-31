"""The two places FRITS reaches outside itself on Fritz's computer: Finder and his mail program.

Both only ever run when Fritz clicks a button. FRITS never sends an email by itself:
it opens a ready-made message in Mail, and Fritz presses Send there.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

# Arguments go in through `argv`, never pasted into the script, so a customer name
# with quotes in it can't change what the script does.
_MAIL_SCRIPT = """
on run argv
    set {theTo, theSubject, theBody, theFile} to argv
    tell application "Mail"
        set m to make new outgoing message with properties ¬
            {subject:theSubject, content:theBody & return & return, visible:true}
        tell m
            if theTo is not "" then
                make new to recipient at end of to recipients with properties {address:theTo}
            end if
            if theFile is not "" then
                make new attachment with properties {file name:(POSIX file theFile)} ¬
                    at after the last paragraph
            end if
        end tell
        activate
    end tell
end run
"""


@dataclass(frozen=True)
class Composed:
    opened: bool  # True: Mail shows the message, Fritz only has to press Send
    mailto: str  # fallback link for other mail programs (cannot carry the attachment)


def mailto_link(to: str, subject: str, body: str) -> str:
    return f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"


def compose_email(to: str, subject: str, body: str, attachment: Path | None) -> Composed:
    link = mailto_link(to, subject, body)
    if sys.platform != "darwin":
        return Composed(False, link)
    args = [to or "", subject, body, str(attachment) if attachment else ""]
    try:
        subprocess.run(
            ["osascript", "-", *args],
            input=_MAIL_SCRIPT,
            text=True,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return Composed(False, link)
    return Composed(True, link)


def open_path(path: Path) -> bool:
    """Show a folder in Finder (or open a file with its usual app)."""
    path = Path(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True, timeout=10)
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", str(path)], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return True

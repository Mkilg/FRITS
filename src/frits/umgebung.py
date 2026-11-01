"""Where FRITS runs: as an app on the Mac (the normal case) or inside a browser tab.

The browser version (see frits/browser) sets IM_BROWSER = True before the app starts. Only the few
sentences that name the Mac's Keychain or Finder look at it, so nobody is told something untrue
about where their key or their files are.
"""

from __future__ import annotations

IM_BROWSER = False


def schluessel_ort() -> str:
    """Where the AI key is kept, as the end of "Dein Schlüssel liegt jetzt …"."""
    if IM_BROWSER:
        return "nur in diesem Browser auf diesem Gerät (nie in einer Sicherung)"
    return "im Schlüsselbund dieses Macs"

#!/bin/bash
# FRITS in a Terminal window: shows the welcome text, then lets you type "frits ..." commands.
RES="$(cd "$(dirname "$0")" && pwd)"
export PATH="$RES/bin:$PATH"
clear
cat <<'BANNER'

   _______
  | | | | |    FRITS
  | | | | |    Fantastisches Rechnungsschreibungs IT-System
  |_|_|_|_|
   \     /     Hallo! Tippe zum Beispiel:  frits --help
    \___/

BANNER
frits --help
echo
if [ -n "$ZSH_VERSION" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
  exec /bin/zsh -i
fi
exec /bin/bash -i

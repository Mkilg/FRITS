#!/bin/bash
# Double-click to open FRITS in a Terminal window.
exec "$(cd "$(dirname "$0")" && pwd)/FRITS.app/Contents/Resources/frits-terminal.command"

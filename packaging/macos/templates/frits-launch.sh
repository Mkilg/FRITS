#!/bin/bash
# Runs FRITS with the Python bundled inside FRITS.app. Nothing is installed on the Mac.
RES="$(cd "$(dirname "$0")" && pwd)"

# Apple Silicon Macs use the arm64 Python, Intel Macs the x86_64 one.
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] && [ -x "$RES/runtime/arm64/bin/python3" ]; then
  ARCH=arm64
else
  ARCH=x86_64
fi
PY="$RES/runtime/$ARCH/bin/python3"
if [ ! -x "$PY" ]; then
  echo "FRITS: no Python for this Mac ($ARCH) found in $RES/runtime" >&2
  exit 1
fi

# Shared German language model; ignore anything the Mac's own Python setup might add.
export PYTHONPATH="$RES/shared"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME VIRTUAL_ENV
# The folder that holds FRITS.app (for example the USB stick), for keeping data next to it.
export FRITS_PORTABLE_DIR="${FRITS_PORTABLE_DIR:-$(cd "$RES/../../.." && pwd)}"

if [ "$(uname -m)" != "$ARCH" ] && [ "$ARCH" = "arm64" ]; then
  exec arch -arm64 "$PY" -m frits.cli "$@"
fi
exec "$PY" -m frits.cli "$@"

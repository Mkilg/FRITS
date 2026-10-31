#!/usr/bin/env bash
# Builds a self-contained FRITS for macOS that runs offline, e.g. from a USB stick.
#
# Result: dist/macos/FRITS-macOS.zip containing
#   FRITS/FRITS.app           double-click to start
#   FRITS/Start-FRITS.command opens FRITS in a Terminal window
#   FRITS/LIESMICH.txt        short guide in German
#
# Inside FRITS.app sit two copies of Python (Apple Silicon and Intel), each with
# FRITS and its libraries, plus one shared copy of the German language model.
# The launcher picks the right Python for the Mac it is running on.
#
# Usage (from the repo root):  packaging/macos/build.sh
# Needs: uv. Works on macOS and Linux (Linux only for testing the layout).
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.12}"
ARCHES="${ARCHES:-arm64 x86_64}"
MODEL_URL="${MODEL_URL:-https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl}"
# Oldest macOS the bundled libraries must support (Ventura).
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$REPO/packaging/macos"
OUT="$REPO/dist/macos"
WORK="$OUT/work"
STAGE="$OUT/FRITS"
APP="$STAGE/FRITS.app"
RES="$APP/Contents/Resources"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"

echo "==> Building FRITS $VERSION for macOS ($ARCHES)"
rm -rf "$OUT"
mkdir -p "$WORK" "$RES/runtime" "$RES/shared" "$APP/Contents/MacOS"

# 1. FRITS itself as a wheel, plus the exact library versions from uv.lock.
echo "==> Building the FRITS wheel"
(cd "$REPO" && uv build --wheel --out-dir "$WORK/wheel" >/dev/null)
(cd "$REPO" && uv export --frozen --no-dev --no-hashes --no-emit-project \
    --format requirements-txt -o "$WORK/requirements.txt" >/dev/null)

# 2. One standalone Python per chip type, with FRITS installed into it.
for arch in $ARCHES; do
  case "$arch" in
    arm64)  uv_arch=aarch64 ;;
    x86_64) uv_arch=x86_64 ;;
    *) echo "Unknown arch: $arch" >&2; exit 1 ;;
  esac
  echo "==> Python $PY_VERSION for $arch"
  uv python install "cpython-${PY_VERSION}-macos-${uv_arch}-none" \
      --install-dir "$WORK/pythons" --no-bin >/dev/null
  src="$(find "$WORK/pythons" -maxdepth 1 -type d -name "cpython-${PY_VERSION}.*-macos-${uv_arch}-none" | head -1)"
  [ -n "$src" ] || { echo "Python for $arch not found" >&2; exit 1; }
  dest="$RES/runtime/$arch"
  cp -R "$src" "$dest"
  site="$dest/lib/python${PY_VERSION}/site-packages"

  echo "==> Libraries for $arch"
  uv pip install --quiet --no-deps --target "$site" \
      --python-version "$PY_VERSION" --python-platform "${uv_arch}-apple-darwin" \
      -r "$WORK/requirements.txt" "$WORK"/wheel/*.whl

  # Trim parts of Python FRITS never uses, to keep the stick small.
  rm -rf "$dest/lib/python${PY_VERSION}/test" "$dest/lib/python${PY_VERSION}/idlelib" \
         "$dest/share" "$dest/include"
  rm -rf "$site"/bin
done

# 3. The German language model, once, shared by both Pythons (it is plain data).
echo "==> German language model (about 570 MB)"
curl -fsSL --retry 4 -o "$WORK/de_core_news_lg-3.8.0-py3-none-any.whl" "$MODEL_URL"
uv pip install --quiet --no-deps --target "$RES/shared" \
    --python-version "$PY_VERSION" "$WORK/de_core_news_lg-3.8.0-py3-none-any.whl"
rm -rf "$RES/shared/bin"

# 4. USB sticks (exFAT/FAT32) cannot store symbolic links: replace each with a real copy.
echo "==> Replacing symbolic links with real files"
while IFS= read -r link; do
  target="$(cd "$(dirname "$link")" && cd "$(dirname "$(readlink "$link")")" && pwd)/$(basename "$(readlink "$link")")"
  rm "$link"
  cp -RL "$target" "$link"
done < <(find "$APP" -type l)

# 5. Launchers, app description and guide.
echo "==> Launchers"
cp "$HERE/templates/frits-launch.sh"      "$RES/frits-launch.sh"
cp "$HERE/templates/frits-terminal.command" "$RES/frits-terminal.command"
cp "$HERE/templates/FRITS"                "$APP/Contents/MacOS/FRITS"
cp "$HERE/templates/Start-FRITS.command"  "$STAGE/Start-FRITS.command"
cp "$HERE/templates/LIESMICH.txt"         "$STAGE/LIESMICH.txt"
mkdir -p "$RES/bin"
printf '#!/bin/bash\nexec "$(cd "$(dirname "$0")/.." && pwd)/frits-launch.sh" "$@"\n' > "$RES/bin/frits"
sed "s/@VERSION@/$VERSION/g" "$HERE/templates/Info.plist" > "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
chmod +x "$RES/frits-launch.sh" "$RES/frits-terminal.command" "$RES/bin/frits" \
         "$APP/Contents/MacOS/FRITS" "$STAGE/Start-FRITS.command"

# Optional app icon: packaging/macos/FRITS.icns, or icon.png (1024x1024) converted on macOS.
if [ -f "$HERE/FRITS.icns" ]; then
  cp "$HERE/FRITS.icns" "$RES/FRITS.icns"
elif [ -f "$HERE/icon.png" ] && command -v iconutil >/dev/null; then
  set_dir="$WORK/FRITS.iconset"; mkdir -p "$set_dir"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$HERE/icon.png" --out "$set_dir/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$HERE/icon.png" --out "$set_dir/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$set_dir" -o "$RES/FRITS.icns"
fi

# 6. Pre-compile Python files so a read-only or slow USB stick starts quickly.
#    The compiled files work for both chip types.
echo "==> Pre-compiling"
compiler="$(uv python find "$PY_VERSION")"
"$compiler" -m compileall -q -j 0 --invalidation-mode unchecked-hash \
    "$RES/shared" $(for a in $ARCHES; do echo "$RES/runtime/$a/lib/python${PY_VERSION}"; done) \
    >/dev/null || true

# 7. Zip it. ditto keeps the "may run" flags that Finder needs.
echo "==> Zipping"
if command -v ditto >/dev/null; then
  ditto -c -k --keepParent "$STAGE" "$OUT/FRITS-macOS.zip"
else
  (cd "$OUT" && zip -qry FRITS-macOS.zip FRITS)
fi
rm -rf "$WORK"
du -sh "$STAGE" "$OUT/FRITS-macOS.zip"
echo "==> Done: $OUT/FRITS-macOS.zip"

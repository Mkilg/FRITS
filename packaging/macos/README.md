# FRITS for Mac

This folder turns FRITS into something you can hand to a friend on a USB stick:
double-click and it runs, with no installing, no Terminal setup and no internet.

## What you get

`FRITS-macOS.zip` (about 730 MB, 1.2 GB unpacked) with a folder `FRITS` inside:

| File | What it is |
| --- | --- |
| `FRITS.app` | Double-click to start. Opens the FRITS window once `frits ui` exists; until then it opens a Terminal window. |
| `Start-FRITS.command` | Opens FRITS in a Terminal window, where you can type `frits ...` commands. |
| `LIESMICH.txt` | Short German guide for Fritz, including the first-start security prompt. |

It works on Apple Silicon (M1 and newer) and Intel Macs with macOS 13 or newer.

## How it works

`FRITS.app` is a normal folder that macOS shows as an app. Inside `Contents/Resources`:

- `runtime/arm64` and `runtime/x86_64`: two complete copies of Python 3.12
  ([python-build-standalone](https://github.com/astral-sh/python-build-standalone), fetched by uv),
  one per chip type, each with FRITS and the exact library versions from `uv.lock`.
- `shared`: the German language model `de_core_news_lg` (about 570 MB). It is plain data, so both Pythons share one copy.
- `tesseract/arm64` and `tesseract/x86_64`: Tesseract, the program that reads text from photos of receipts
  (from conda-forge, fetched with micromamba), with only the libraries it needs.
  `tessdata` holds its German and English language data.
- `frits-launch.sh`: picks the Python that fits the Mac and runs `python -m frits.cli`.
  It sets `FRITS_TESSERACT` (the Tesseract program) and `TESSDATA_PREFIX` (its language data).

Fritz's own data (customers, invoices) is not stored in the app or on the stick but on his Mac,
in `~/Documents/FRITS` (or wherever `FRITS_HOME` points).

USB sticks usually cannot store "shortcuts" (symbolic links), so the build replaces every one with a real file.

## Getting the zip

GitHub builds it for you (workflow `Mac app` in `.github/workflows/macos-app.yml`):

- On the repository page, open **Actions**, click **Mac app**, open the newest run and download **FRITS-macOS** at the bottom.
  GitHub wraps the zip in another zip, so you unzip twice. Downloads are kept for 3 days.
- For a lasting download, create a version tag such as `v1.0.0`. The zip is then attached to a GitHub Release.

To build it yourself on a Mac (needs [uv](https://docs.astral.sh/uv/) and `brew install micromamba`):

```sh
packaging/macos/build.sh        # result: dist/macos/FRITS-macOS.zip
```

## Putting it on a USB stick

1. Unzip `FRITS-macOS.zip`.
2. macOS marks downloaded files as "from the internet" and will block them. Remove that mark once, in Terminal:
   `xattr -dr com.apple.quarantine ` then drag the `FRITS` folder into the window and press Enter.
3. Copy the `FRITS` folder onto the stick.

FRITS is not signed with a paid Apple developer certificate, so on another Mac the first start may still say it
"cannot be opened". Then: System Settings > Privacy & Security > "Open Anyway". `LIESMICH.txt` explains this in German.

## Options

- App icon: put `FRITS.icns`, or a 1024×1024 `icon.png`, into this folder and rebuild.
- `ARCHES=arm64 packaging/macos/build.sh` builds only the Apple Silicon version (smaller, faster to test).

## Not included yet

- No Apple signing/notarization (needs a paid Apple developer account).

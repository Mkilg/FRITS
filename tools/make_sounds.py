"""A tiny synthesizer that makes all of FRITS's sounds.

Run it from the repo root to rebuild the files in src/frits/assets/sounds/:

    python3 tools/make_sounds.py

It needs ffmpeg (inside the Docker image, or `brew install ffmpeg`) to turn the
WAV files into small MP3s that every browser, including Safari, can play.
Fritz never runs this: the finished MP3s are committed to the repo.

Every sound in FRITS is made by this code. Nothing is downloaded and no sample
comes from someone else, so there is no license to check: the sounds are under
the same MIT license as the rest of FRITS. Only the Python standard library is
used, so the result is the same on every machine.

The design follows the "Making paperwork fun" research: instrumental only, slow,
soft, no sudden loud parts. Cues are short and quiet ("Erledigt!", not a fanfare).
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

RATE = 22050  # samples per second; plenty for soft sounds and keeps files small
TAU = 2 * math.pi


def note(name: str) -> float:
    """Frequency in Hz for a note name like 'A4', 'C#5' or 'Bb3'."""
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    letter, rest = name[0], name[1:]
    step = names[letter]
    if rest.startswith("#"):
        step, rest = step + 1, rest[1:]
    elif rest.startswith("b"):
        step, rest = step - 1, rest[1:]
    octave = int(rest)
    return 440.0 * 2 ** ((step + 12 * (octave - 4)) / 12)


def _silence(seconds: float) -> list[float]:
    return [0.0] * int(seconds * RATE)


def _mix_into(buf: list[float], sound: list[float], start: float, gain: float = 1.0) -> None:
    """Add `sound` into `buf` starting at `start` seconds, wrapping around the end.

    Wrapping makes music loops seamless: a note that rings past the end of the
    loop continues at its beginning.
    """
    offset = int(start * RATE)
    n = len(buf)
    for i, s in enumerate(sound):
        buf[(offset + i) % n] += s * gain


def bell(freq: float, seconds: float = 1.2, brightness: float = 0.35) -> list[float]:
    """A soft mallet/bell tone: quick but not clicky attack, smooth exponential fade."""
    n = int(seconds * RATE)
    attack = int(0.008 * RATE)
    decay = 3.2 / seconds
    partials = [(1.0, 1.0), (2.0, brightness), (3.01, brightness * 0.35), (4.2, brightness * 0.12)]
    out = []
    for i in range(n):
        t = i / RATE
        env = math.exp(-decay * t) * (min(1.0, i / attack) if attack else 1.0)
        s = 0.0
        for mult, amp in partials:
            # higher partials fade faster, like a real mallet instrument
            s += amp * math.exp(-decay * mult * 0.6 * t) * math.sin(TAU * freq * mult * t)
        out.append(s * env)
    return out


def pad(freq: float, seconds: float, attack: float = 2.5, release: float = 2.5) -> list[float]:
    """A warm, slowly swelling tone with a gentle chorus (two slightly detuned voices)."""
    n = int(seconds * RATE)
    a, r = int(attack * RATE), int(release * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        if i < a:
            env = 0.5 - 0.5 * math.cos(math.pi * i / a)
        elif i > n - r:
            env = 0.5 - 0.5 * math.cos(math.pi * (n - i) / r)
        else:
            env = 1.0
        s = (
            math.sin(TAU * freq * t)
            + math.sin(TAU * freq * 1.003 * t + 1.3)
            + 0.18 * math.sin(TAU * freq * 2 * t)
            + 0.06 * math.sin(TAU * freq * 3 * t)
        )
        out.append(s * env * 0.4)
    return out


def _lowpass(buf: list[float], cutoff: float) -> list[float]:
    """One-pole low-pass filter: takes the edge off, makes it warmer."""
    k = 1 - math.exp(-TAU * cutoff / RATE)
    y = 0.0
    out = []
    for s in buf:
        y += k * (s - y)
        out.append(y)
    return out


def _normalize(buf: list[float], peak: float) -> list[float]:
    top = max((abs(s) for s in buf), default=0.0) or 1.0
    return [s * peak / top for s in buf]


def write_wav(path: Path, buf: list[float]) -> Path:
    """Write mono 16-bit WAV. macOS (afplay, Safari, QuickTime) plays it natively."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = struct.pack(f"<{len(buf)}h", *(int(max(-1.0, min(1.0, s)) * 32767) for s in buf))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return path


# --- Cues -------------------------------------------------------------------
# Short, quiet, honest. Peak level is well below full scale on purpose.

CUE_PEAK = 0.35


def cue_done() -> list[float]:
    """'Erledigt!' A small step is done: two soft rising notes (a fourth)."""
    buf = _silence(1.3)
    _mix_into(buf, bell(note("G5"), 0.9), 0.0, 0.8)
    _mix_into(buf, bell(note("C6"), 1.1), 0.11)
    return _normalize(_lowpass(buf, 4500), CUE_PEAK)


def cue_sent() -> list[float]:
    """Invoice sent: a light rising major arpeggio, like a letter leaving."""
    buf = _silence(1.8)
    for k, name in enumerate(["C5", "E5", "G5", "C6"]):
        _mix_into(buf, bell(note(name), 1.2, 0.25), k * 0.09, 0.7 + 0.1 * k)
    return _normalize(_lowpass(buf, 4000), CUE_PEAK)


def cue_shutdown() -> list[float]:
    """Feierabend-Ritual: a slow, descending three-note chime. Work is put away."""
    buf = _silence(3.6)
    for k, name in enumerate(["G5", "E5", "C5"]):
        _mix_into(buf, bell(note(name), 2.4, 0.3), k * 0.45, 0.9)
    _mix_into(buf, pad(note("C4"), 3.4, 0.6, 2.4), 0.6, 0.25)
    return _normalize(_lowpass(buf, 3200), CUE_PEAK)


def cue_focus_start() -> list[float]:
    """Start of a focus block: one single, low, round tone."""
    buf = _silence(2.0)
    _mix_into(buf, bell(note("C5"), 1.9, 0.2), 0.0)
    _mix_into(buf, bell(note("C4"), 1.9, 0.1), 0.0, 0.5)
    return _normalize(_lowpass(buf, 2500), CUE_PEAK)


CUES = {
    "done": cue_done,
    "sent": cue_sent,
    "shutdown": cue_shutdown,
    "focus": cue_focus_start,
}


# --- Music ------------------------------------------------------------------
# Slow ambient loops: pad chords plus a few sparse mallet notes. No beat, no
# lyrics, no melody that pulls attention. Each track loops seamlessly.

MUSIC_PEAK = 0.22  # quiet background; the Mac volume slider does the rest

TRACKS: dict[str, dict] = {
    "morgen": {
        "title": "Morgen (calm start)",
        "chords": [
            ["C3", "G3", "E4", "B4"],
            ["A2", "E3", "C4", "G4"],
            ["F2", "C3", "A3", "E4"],
            ["G2", "D3", "B3", "D4"],
        ],
        "scale": ["C5", "D5", "E5", "G5", "A5"],
        "bar": 9.0,
        "repeats": 2,
        "seed": 7,
        "cutoff": 1800,
    },
    "buero-block": {
        "title": "Büro-Block (steady focus)",
        "chords": [
            ["D3", "A3", "F4", "C5"],
            ["Bb2", "F3", "D4", "A4"],
            ["G2", "D3", "Bb3", "F4"],
            ["A2", "E3", "C#4", "E4"],
        ],
        "scale": ["D5", "E5", "F5", "A5", "C6"],
        "bar": 10.0,
        "repeats": 2,
        "seed": 11,
        "cutoff": 1500,
    },
}


def music_track(name: str) -> list[float]:
    spec = TRACKS[name]
    bar = spec["bar"]
    chords = spec["chords"] * spec["repeats"]
    rng = random.Random(spec["seed"])  # fixed seed: the same track every time
    buf = _silence(bar * len(chords))
    for k, chord in enumerate(chords):
        start = k * bar
        # chords overlap by their release, so changes are a slow crossfade
        for name_ in chord:
            _mix_into(buf, pad(note(name_), bar + 3.0, 3.0, 3.0), start - 1.5, 0.5)
        # two or three gentle notes per bar, at relaxed, slightly uneven times
        for _ in range(rng.choice([1, 2, 2, 3])):
            t = start + rng.uniform(0.5, bar - 1.0)
            _mix_into(buf, bell(note(rng.choice(spec["scale"])), 3.5, 0.2), t, 0.45)
    return _normalize(_lowpass(buf, spec["cutoff"]), MUSIC_PEAK)


# --- Build --------------------------------------------------------------------

OUT = Path(__file__).resolve().parent.parent / "src" / "frits" / "assets" / "sounds"


def _to_mp3(wav: Path, mp3: Path, bitrate: str) -> None:
    import subprocess

    mp3.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(wav),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-map_metadata",
            "-1",
            str(mp3),
        ],
        check=True,
    )


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        for name, make in CUES.items():
            wav = write_wav(Path(tmp) / f"{name}.wav", make())
            _to_mp3(wav, OUT / "cues" / f"{name}.mp3", "64k")
            print("cue  ", name)
        for name in TRACKS:
            wav = write_wav(Path(tmp) / f"{name}.wav", music_track(name))
            _to_mp3(wav, OUT / "music" / f"{name}.mp3", "64k")
            print("music", name)


if __name__ == "__main__":
    main()

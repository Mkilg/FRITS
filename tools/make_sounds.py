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


def pluck(freq: float, seconds: float = 1.6, damping: float = 0.996, seed: int = 0) -> list[float]:
    """A plucked string (Karplus-Strong): a short noise burst ringing in a tuned delay line.

    Sounds like a soft harp or nylon guitar. The delay is fractional (read between two
    samples), so high notes stay in tune. A fixed seed keeps every build identical.
    """
    rng = random.Random(seed)
    delay = RATE / freq - 0.5  # the two-point average below adds half a sample
    start = int(delay) + 2
    out = [rng.uniform(-1, 1) for _ in range(start)]
    for _ in range(2):  # soften the burst so the attack isn't scratchy
        out = [(out[i] + out[i - 1]) / 2 for i in range(start)]
    mean = sum(out) / start
    out = [s - mean for s in out]  # no DC offset, or the speaker cone just sits pushed out
    n = int(seconds * RATE)
    whole, frac = int(delay), delay - int(delay)
    for i in range(start, n):
        j = i - whole
        a = out[j] * (1 - frac) + out[j - 1] * frac  # sample `delay` ago
        b = out[j - 1] * (1 - frac) + out[j - 2] * frac  # one more sample back
        out.append(damping * 0.5 * (a + b))
    out = out[:n]
    # fade the last bit so the note never clicks off
    tail = min(len(out), int(0.05 * RATE))
    for k in range(tail):
        out[len(out) - tail + k] *= 1 - k / tail
    return out


def drum(
    freq: float = 90.0, seconds: float = 0.5, drop: float = 1.8, decay: float = 9.0
) -> list[float]:
    """A soft low drum (kick or tom): a sine whose pitch falls quickly, no click."""
    n = int(seconds * RATE)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / RATE
        f = freq * (1 + drop * math.exp(-t * 30))
        phase += TAU * f / RATE
        env = math.exp(-decay * t) * min(1.0, i / (0.003 * RATE))
        out.append(math.sin(phase) * env)
    return out


def shaker(seconds: float = 0.09, seed: int = 0) -> list[float]:
    """A tiny shaker tick: noise with a fast fade (mixed in very quietly)."""
    rng = random.Random(seed)
    n = int(seconds * RATE)
    return [rng.uniform(-1, 1) * (1 - i / n) ** 3 for i in range(n)]


def horn(freq: float, seconds: float, attack: float = 0.35, release: float = 0.6) -> list[float]:
    """A warm brass-like tone: it gets brighter as it swells, like a soft French horn."""
    n = int(seconds * RATE)
    a, r = max(1, int(attack * RATE)), max(1, int(release * RATE))
    out = []
    phase = 0.0
    for i in range(n):
        t = i / RATE
        if i < a:
            env = 0.5 - 0.5 * math.cos(math.pi * i / a)
        elif i > n - r:
            env = 0.5 - 0.5 * math.cos(math.pi * (n - i) / r)
        else:
            env = 1.0
        # a slight vibrato that only sets in once the note is held
        vib = 1 + 0.003 * math.sin(TAU * 5.0 * t) * min(1.0, t / 0.6)
        phase += TAU * freq * vib / RATE
        s = 0.0
        for k in range(1, 7):
            # upper harmonics grow with the envelope: the "blare" of brass, kept gentle
            s += (env ** (k * 0.5)) / k * math.sin(k * phase)
        out.append(s * env * 0.5)
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
    """Feierabend! A small, warm fanfare: a rising harp run lands on a bright major
    chord with a soft horn and a low drum, then rings out like evening bells.
    Plays right on the click, so the best part fits into the first two seconds."""
    buf = _silence(4.4)
    run = ["C4", "E4", "G4", "C5", "E5", "G5"]
    for k, name in enumerate(run):
        _mix_into(buf, pluck(note(name), 2.2, 0.997, seed=40 + k), k * 0.065, 0.55)
    land = 6 * 0.065 + 0.04
    _mix_into(buf, drum(70, 1.2, 1.2, 4.0), land, 0.7)
    for name in ["C4", "G4", "E5"]:
        _mix_into(buf, horn(note(name), 1.9, 0.08, 1.2), land, 0.32)
    for k, name in enumerate(["C6", "G6", "E6"]):
        _mix_into(buf, bell(note(name), 3.2, 0.25), land + 0.12 + k * 0.22, 0.45 - 0.08 * k)
    _mix_into(buf, pad(note("C3"), 3.4, 0.2, 2.6), land, 0.35)
    fade = int(1.0 * RATE)  # let the bells die away instead of stopping
    for k in range(fade):
        buf[len(buf) - fade + k] *= 1 - k / fade
    return _normalize(_lowpass(buf, 5200), 0.45)


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
    "schwung": {"title": "Schwung (motivating groove)", "make": lambda: schwung()},
    "abenteuer": {"title": "Abenteuer (Steuer-Quest)", "make": lambda: abenteuer()},
}


def music_track(name: str) -> list[float]:
    spec = TRACKS[name]
    if "make" in spec:
        return spec["make"]()
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


def schwung() -> list[float]:
    """Motivierend: a light, steady groove at 100 BPM in G major. A soft kick on every
    beat, a quiet shaker, a plucked arpeggio and warm chords. Happy, but no melody that
    asks for attention, so it still works while writing invoices."""
    beat = 60 / 100
    bar = 4 * beat
    chords = [  # G - D - Em - C, the "everything is going well" progression
        (["G2"], ["G3", "B3", "D4"], ["G4", "B4", "D5", "B4"]),
        (["D3"], ["F#3", "A3", "D4"], ["F#4", "A4", "D5", "A4"]),
        (["E2"], ["G3", "B3", "E4"], ["E4", "G4", "B4", "G4"]),
        (["C3"], ["G3", "C4", "E4"], ["E4", "G4", "C5", "G4"]),
    ]
    bars = chords * 4
    buf = _silence(bar * len(bars))
    seed = 0
    for k, (bass, chord, arp) in enumerate(bars):
        start = k * bar
        for name in chord:
            _mix_into(buf, pad(note(name), bar + 1.0, 0.4, 1.0), start - 0.2, 0.22)
        for b in range(4):
            t = start + b * beat
            _mix_into(buf, drum(55, 0.45, 1.6, 10.0), t, 0.55 if b in (0, 2) else 0.35)
            _mix_into(buf, shaker(0.07, seed=b), t + beat / 2, 0.05)
            _mix_into(buf, pluck(note(bass[0]), beat * 1.6, 0.995, seed=seed), t, 0.35)
            seed += 1
        # eighth-note arpeggio; the second half of the song gets a lift an octave up
        lift = 1.0 if k < 8 else 2.0
        for e in range(8):
            name = arp[e % 4]
            _mix_into(
                buf,
                pluck(note(name) * lift, 1.2, 0.996, seed=seed),
                start + e * beat / 2,
                0.30 if e % 2 == 0 else 0.22,
            )
            seed += 1
    return _normalize(_lowpass(buf, 3800), MUSIC_PEAK)


def abenteuer() -> list[float]:
    """Abenteuerlich, for the Steuer-Quest: D minor, 80 BPM. Low drums in a marching
    rhythm, a harp running up and down, and a horn melody that sounds like a map being
    unrolled. Still soft and loopable, so filling in tax forms doesn't get stressful."""
    beat = 60 / 80
    bar = 4 * beat
    # Dm - Bb - F - C : "the journey begins", then Dm - Bb - C - A : "something waits ahead"
    harmony = [
        ("D2", ["D3", "A3", "F4"]),
        ("Bb1", ["Bb2", "F3", "D4"]),
        ("F2", ["F3", "C4", "A4"]),
        ("C2", ["C3", "G3", "E4"]),
        ("D2", ["D3", "A3", "F4"]),
        ("Bb1", ["Bb2", "F3", "D4"]),
        ("C2", ["C3", "G3", "E4"]),
        ("A1", ["A2", "E3", "C#4"]),
    ]
    # horn melody, one entry per bar: (note, start beat, length in beats)
    melody = [
        [("A4", 0, 1.5), ("D5", 1.5, 0.5), ("E5", 2, 1), ("F5", 3, 1)],
        [("F5", 0, 2), ("D5", 2, 2)],
        [("C5", 0, 1.5), ("F5", 1.5, 0.5), ("A5", 2, 2)],
        [("G5", 0, 2), ("E5", 2, 2)],
        [("A4", 0, 1.5), ("D5", 1.5, 0.5), ("E5", 2, 1), ("F5", 3, 1)],
        [("G5", 0, 1.5), ("F5", 1.5, 0.5), ("D5", 2, 2)],
        [("E5", 0, 1), ("G5", 1, 1), ("C6", 2, 2)],
        [("A5", 0, 3), ("E5", 3, 1)],
    ]
    sections = [False, True]  # first time drums and harp only, then the horn joins
    buf = _silence(bar * len(harmony) * len(sections))
    seed = 100
    for s_i, with_horn in enumerate(sections):
        for k, (root, chord) in enumerate(harmony):
            start = (s_i * len(harmony) + k) * bar
            for name in chord:
                _mix_into(buf, pad(note(name), bar + 1.2, 0.6, 1.2), start - 0.3, 0.25)
            _mix_into(buf, pad(note(root), bar + 0.8, 0.3, 0.8), start - 0.1, 0.35)
            # marching drums: BOOM . ba-da BOOM . ba-da
            for t_beat, gain, f in [
                (0, 0.8, 60),
                (1.5, 0.35, 95),
                (1.75, 0.4, 95),
                (2, 0.7, 60),
                (3.5, 0.35, 95),
                (3.75, 0.45, 110),
            ]:
                _mix_into(buf, drum(f, 0.6, 1.4, 7.0), start + t_beat * beat, gain)
            # harp: up and down the chord in eighth notes
            tones = [note(n) * 2 for n in chord]
            up = tones + [tones[0] * 2] + tones[::-1][1:] + [tones[1]]
            for e in range(8):
                _mix_into(
                    buf, pluck(up[e % len(up)], 1.4, 0.996, seed=seed), start + e * beat / 2, 0.22
                )
                seed += 1
            if with_horn:
                for name, b0, length in melody[k]:
                    _mix_into(
                        buf,
                        horn(note(name), length * beat + 0.15, 0.12, 0.35),
                        start + b0 * beat,
                        0.42,
                    )
    return _normalize(_lowpass(buf, 4200), MUSIC_PEAK)


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

# Sounds in FRITS: where they come from

Every sound in this folder was **made by FRITS's own code**
(`tools/make_sounds.py`). No recordings, samples or loops from anyone else are
used, so there is nobody else's license to follow.

License: **MIT**, the same as the rest of FRITS (see `LICENSE` in the repo root).
You may also treat these files as **CC0** (public domain) if that is easier.

| File | What it is | Made by |
|---|---|---|
| `cues/done.mp3` | "Erledigt!" – two soft rising notes | `cue_done()` |
| `cues/sent.mp3` | Invoice sent – light rising arpeggio | `cue_sent()` |
| `cues/shutdown.mp3` | Feierabend – small warm fanfare: harp run, horn chord, evening bells | `cue_shutdown()` |
| `cues/focus.mp3` | Start of a focus block – one low tone | `cue_focus_start()` |
| `music/morgen.mp3` | Calm ambient loop, C major, ~72 s | `music_track("morgen")` |
| `music/buero-block.mp3` | Steady ambient loop, D minor, ~80 s | `music_track("buero-block")` |
| `music/schwung.mp3` | Motivating groove, G major, 100 BPM, ~38 s | `schwung()` |
| `music/abenteuer.mp3` | Adventure loop for the Steuer-Quest, D minor, 80 BPM, ~48 s | `abenteuer()` |

To change a sound, edit `tools/make_sounds.py` and run
`python3 tools/make_sounds.py` (needs ffmpeg). If you ever add a sound from
somewhere else, only use CC0 audio and add a row here with its source link.

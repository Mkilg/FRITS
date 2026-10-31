# FRITS, die Pommes

Five moods, each a 240x240 SVG with no external fonts or images:

| File | When to show it |
|---|---|
| `welcome.svg` | greeting, first start, "willkommen zurück" |
| `thinking.svg` | FRITS is reading or the user has a choice to make |
| `done.svg` | "Erledigt!" after a draft or invoice is finished |
| `warning.svg` | something needs the user's decision (calm, not an alarm) |
| `feierabend.svg` | shutdown ritual at the end of a Büro-Block |

`lines.json` holds the German texts: `steps` for the setup assistant, `moments` for everyday messages.
Use them from Python with `frits.mascot.mascot_svg(mood)`, `step(key)` and `line(key)`.

Tone: calm and friendly, per du, no guilt, no exclamation-mark storms.

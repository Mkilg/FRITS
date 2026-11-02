"""One small goal for the day, the same everywhere (Lena: Tagwerk and "Noch 3 Karten für
Fritte heute" were one goal under two names).

Three things of real work a day, then it's done. Slow productivity: the goal is small on
purpose, and missing it costs nothing. What counts:
- Fritte's cards of real paperwork that FRITS noticed today (invoice, payment, expense,
  quote, reminder),
- Steuer-Quest pieces found or asked about today (the quest's own rule: "Gibt's nicht"
  does not count, or stamping things away would be the fastest way to the goal).
Fritte's own cards for tax pieces are left out, so one find counts once.

Read only: it never writes, so the Steuer-Quest can ask inside its own transaction.
"""

from __future__ import annotations

from datetime import date

from frits.spiel import stand
from frits.spiel.gewuerze import PAPIERKRAM

ZIEL = 3
# The same rule as Fritte's chests (Lena): ticking off the list is good, but it is no
# paperwork; it gives Fritte its own small cards instead.
ARTEN = PAPIERKRAM
ERKLAERUNG = (
    "Es zählt echter Papierkram von heute: Rechnungen, Zahlungen, Ausgaben, Angebote,"
    " Erinnerungen und Funde in der Steuer-Quest. Wie bei Frittes Truhen zählen abgehakte"
    " Punkte hier nicht."
)


def zaehlen(conn, heute: date) -> dict:
    tag = heute.isoformat()
    karten = stand.rows(
        conn,
        f"SELECT COUNT(*) FROM spiel_karten WHERE gesehen = ? AND art IN ({', '.join('?' * len(ARTEN))})"
        " AND (kassensturz IS NULL OR kassensturz != 0)",
        (tag, *sorted(ARTEN)),
    )
    quest = stand.rows(
        conn,
        "SELECT COUNT(*) FROM steuer_stuecke WHERE status IN ('gesammelt', 'fragen')"
        " AND substr(geaendert, 1, 10) = ?",
        (tag,),
    )
    erledigt = (karten[0][0] if karten else 0) + (quest[0][0] if quest else 0)
    geschafft = erledigt >= ZIEL
    return {
        "erledigt": erledigt,
        "ziel": ZIEL,
        "geschafft": geschafft,
        "text": "Tagesziel geschafft"
        if geschafft
        else f"Tagesziel: {erledigt} von {ZIEL} geschafft",
        "erklaerung": ERKLAERUNG,
    }

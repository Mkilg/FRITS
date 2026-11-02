## FRITS für den Mac

Vorher prüfen: Zum Entpacken braucht der Mac etwa **2,5 GB freien Speicher** (danach 1,4 GB). Reicht der Platz nicht, fehlen am Ende Dateien und FRITS startet nicht.

1. **FRITS-macOS.zip** unten herunterladen und doppelklicken. Es entsteht ein Ordner **FRITS** mit FRITS.app, Start-FRITS.command und LIESMICH.txt.
2. Den Ordner dorthin legen, wo er bleiben soll (z. B. Programme oder USB-Stick).
3. FRITS.app doppelklicken. Beim ersten Mal blockiert der Mac die App, weil sie nicht aus dem App Store kommt:
   - Warnung schließen.
   - **Systemeinstellungen → Datenschutz & Sicherheit** öffnen und ganz nach unten scrollen.
   - Bei „FRITS wurde blockiert“ auf **Trotzdem öffnen** klicken und mit dem Passwort bestätigen.

Sagt der Mac stattdessen, die App sei „beschädigt“: Terminal öffnen, `xattr -dr com.apple.quarantine ` tippen (mit Leerzeichen am Ende), den FRITS-Ordner ins Fenster ziehen und Enter drücken.

Läuft ohne Internet, auf Macs mit Apple-Chip und Intel, ab macOS 13. Deine Daten liegen in `Dokumente/FRITS`, nicht im App-Ordner.

### Neu in 1.6.0
- **Neuer Look:** Jede Seite liegt auf einem Eichenbrett mit hellen Papierkarten. Pro Seite gibt es einen Hauptknopf. Auf dem Handy gibt es unten eine Tab-Leiste.
- **Fritte wird ein richtiges Spiel:** Echter Papierkram füllt Truhen mit Schätzen, Gewürzen und Körnern. Gewürze zeigen ihre Wirkung, dazu kommen ein Tagesziel, ein Saison-Streifen und eine gemeinsame **Sammlung** (unter /sammlung). Das System ist verdichtet auf eine Börse und ein Kassenbuch. Alte Spielstände werden übernommen.
- **Steuer-Quest:** Die Gold-Serie zählt nur mit echten Belegen. Fritte stutzt bei Urlaubsfotos, ein Drachensieg legt eine Truhe ab, und Quest-Töne laufen im Klangrahmen.
- **Ausgaben:** Bücher, Zeitschriften und Fahrkarten ohne aufgedruckten Steuersatz bekommen 7 % vorgeschlagen. Findet FRITS kein Datum, bleibt das Feld leer und der Cursor steht darin.
- **Weniger Sackgassen:** Eine falsche Zahl (zum Beispiel „neunzig“ oder „zwei“) markiert genau das Feld rot, und deine Eingaben bleiben. Rückfragen erscheinen als FRITS-Karte, beim Stornieren mit rotem Knopf. „Ohne KI weitermachen“ ist ein Knopf.

### Neu in 1.5.1
- **Mehr Schutz beim Schwärzen:** Steuer-ID, Sozialversicherungsnummer und eTIN (von der Lohnsteuerbescheinigung) gehen nie an die KI.
- „Kenne ich schon“ führt direkt zum Beleg, Entwurf oder zur Rechnung. Keine doppelten „(1)“-Dateien mehr.
- Erste Sicherung bei gesperrtem Schlüsselbund: FRITS fragt nach einem neuen Passwort. Der gewählte Ort bleibt stehen.
- Betrag auf gescannten Bahntickets und Laden aus E-Mails werden richtig gelesen.
- Entwürfe nutzen auf großen Bildschirmen die ganze Breite.
- Steuer-Quest findet ihre Belege auch nach dem Zurückholen einer Sicherung.

### Neu in 1.5.0
- **Freier Chat** im Menü „Mehr“, auch ohne Dokument. Die Einführung lässt sich über Mehr › Einführung wiederholen neu starten.
- Musik läuft beim Seitenwechsel weiter. Eine fertige Rechnung bekommt Stempel und Klang.
- **Bitte einmal ansehen** sagt jetzt, was zu tun ist: als Beleg eintragen, im Notfall-Chat verstehen oder weglegen. Briefe werden kein 0-€-Entwurf mehr.
- Liest RTF, gescannte PDFs und Anhänge in E-Mails. Fragen im Entwurf kannst du direkt beantworten.
- **Rechnungen von früher nachtragen**, damit EÜR und Umsatzsteuer vollständig sind.
- **Datensicherung** für den Ernstfall neuer Mac: Ordner im Finder wählen, Passwort vom Zettel, Sicherung auch mit Keka öffnen.
- Belege aus dem Ausland ohne deutsche Vorsteuer. Nach drei Erinnerungen hilft „Wie weiter?“.
- Neu aus anderen Bereichen: Steuer-Quest mit Belegbuch, Kurs „So funktionieren KI-Agenten“.

## FRITS für den Mac

Vorher prüfen: Zum Entpacken braucht der Mac etwa **3 GB freien Speicher** (danach 1,6 GB). Reicht der Platz nicht, meldet der Mac „Fehler 640“ oder es fehlen am Ende Dateien und FRITS startet nicht. Alte FRITS-ZIPs und -Ordner aus „Downloads“ kannst du vorher löschen, deine Daten liegen in Dokumente/FRITS.

1. **FRITS-macOS.zip** unten herunterladen und doppelklicken. Es entsteht ein Ordner **FRITS** mit FRITS.app, Start-FRITS.command und LIESMICH.txt.
2. Den Ordner dorthin legen, wo er bleiben soll (z. B. Programme oder USB-Stick).
3. FRITS.app doppelklicken. Beim ersten Mal blockiert der Mac die App, weil sie nicht aus dem App Store kommt:
   - Warnung schließen.
   - **Systemeinstellungen → Datenschutz & Sicherheit** öffnen und ganz nach unten scrollen.
   - Bei „FRITS wurde blockiert“ auf **Trotzdem öffnen** klicken und mit dem Passwort bestätigen.

Klappt das Entpacken per Doppelklick nicht (z. B. „Fehler 640“), geht es im Terminal mit einem klaren Fehlertext: `ditto -x -k ~/Downloads/FRITS-macOS.zip ~/Downloads/`

Sagt der Mac stattdessen, die App sei „beschädigt“: Terminal öffnen, `xattr -dr com.apple.quarantine ` tippen (mit Leerzeichen am Ende), den FRITS-Ordner ins Fenster ziehen und Enter drücken.

Läuft ohne Internet, auf Macs mit Apple-Chip und Intel, ab macOS 13. Deine Daten liegen in `Dokumente/FRITS`, nicht im App-Ordner.

### Neu in 1.7.8

- **Rechnungen am Handy:** Jede Rechnung ist eine Karte, alle Knöpfe passen ins Bild. Öffnet sich Mail nicht, lädst du das PDF direkt herunter.
- **Ausgaben:** Die Kategorie folgt beim Tippen („Deutsche Bahn“ wird Geschäftsreise mit 7 %). Was du selbst wählst, bleibt, auch nach einer Fehlermeldung.
- **Kassensturz:** Die Knöpfe bleiben in jedem Fenster sichtbar, „Überspringen“ ist gut lesbar.
- **Steuer-Quest:** Keine doppelten Zettel mehr im Korb, kein Finder-Knopf am Handy, und Belege aus einem anderen Jahr bekommen einen klaren Ausweg.

### Neu in 1.7.7

- **Erster Start ohne Fehler:** Beim allerersten Öffnen zeigte FRITS manchmal kurz „Internal Server Error“, bis man neu lud. Das passiert nicht mehr.

### Neu in 1.7.6

- **Steuer-Quest: Sicherung zurückholen.** Unter „Neu beginnen“ holst du eine frühere Sicherung zurück. Dabei gehen keine Belege verloren. Der Eingangskorb kennt das Jahr jedes Belegs.
- **Kleines i am Handy:** Es lässt sich leicht antippen und öffnet sich als Zettel unten am Bildschirm, auch unter schiefen Überschriften. Escape schließt nur das i, nicht den ganzen Dialog.
- **Am Handy:** Die Tab-Leiste unten reagiert auch auf der Steuer-Insel. Vorher konnte ein Tipp auf „Heute“ dort etwas auf der Karte öffnen. Knöpfe und Klapp-Zeilen sind groß genug für den Finger. Die Steuer-Quest nutzt dieselben sechs Schriftgrößen wie der Rest.

### Neu in 1.7.5

- **Viel weniger Text:** Die Seiten sind um bis zu zwei Drittel kürzer, es gibt nur noch sechs Schriftgrößen. Was man nicht jedes Mal braucht, steht hinter einem kleinen **i** zum Antippen.
- **Laden mit Formeln:** Statt langer Sätze steht da zum Beispiel „🧂×2 für Fragen“.
- **Dein Vorsatz und Frittes Flamme:** Am Ende der Einführung sagst du, wie oft du dich um den Papierkram kümmern willst. Neben Fritte zählt eine Flamme, wie oft in Folge du es geschafft hast.
- **Einführung für Techies:** Bei Schwärzen und Trick-Test gibt es Ausklapper mit den genauen Bibliotheken und Code. Schritt 9 erklärt, dass nichts verloren geht, weil alles als normale Dateien auf dem Mac liegt.
- **Neu anfangen:** Unter Einstellungen › Neu anfangen startest du mit einem leeren FRITS. Der alte Ordner bleibt als „FRITS-alt-Datum“ erhalten. Die Steuer-Quest lässt sich pro Jahr neu beginnen (Insel › Mehr), auch mit Sicherung.
- **Eingang ohne Häkchen:** Was du ablegst, wird gleich gelesen, vorher geschwärzt. Wer lieber sammelt, stellt das unter Einstellungen › KI um.
- **Ladebild:** Beim Start bereitet Fritte die Küche vor („Schäle Kartoffeln …“).

### Neu in 1.7.4
- **Einrichtung kürzer:** Im Lern-Teil steht „Überspringen“ jetzt oben neben dem Hinweis, dass es nur 3 kurze Schritte sind. Der Trick-Test ist knapper.
- **Steuer-Quest:** Eine leere Fundkiste wird nicht mehr gefeiert. Sie sagt ehrlich, was fehlt. Stutzt Fritte bei einem Beleg, führt ein Knopf zur passenden Karte. Nach „Heute nur senden“ gibt es eine Nachtrag-Mail, und ein Dokument mit der Steuer-ID wird erkannt. Deine Beraterin trägst du einmal für alle Jahre ein. Dein eigener Brieftext bleibt erhalten, und die Mail öffnet sich nur, wenn du auf den Knopf drückst.
- **Fritte:** Im Kassensturz drehen sich die Karten im Bild, und „Würze“ ist gut lesbar. Ein Würze-Zettel zeigt die Wirkung, die Seite fährt weich zur Truhe, und gefundene Salzkörner zeigen das gemalte Salz.
- Das Ablagefeld zeigt überall das gemalte Ablage-Symbol.

### Neu in 1.7.3
- **Ausgaben am Handy:** Jede Ausgabe steht als Zeile mit dem Betrag rechts. Die Summe ist immer zu sehen, ohne seitlich zu wischen.
- **Heute** zeigt den Einstieg in die Steuer-Quest, solange es noch keine gibt. Beim ersten Besuch steht kein „seit letztem Mal“ mehr da.
- **Fritte:** Der Kassensturz liegt auf einem gemalten Schreibtisch. Körner und Würze sind Papierkarten, und Steuer-Karten zeigen ihre Provinz. Gekaufte Dinge stehen in den gemalten Zimmern an passenden Plätzen, und statt Emoji gibt es gemalte Bilder. Am Handy bleibt der Stempel im Bild.
- **Steuer-Quest:** Die Fundkiste hat ein richtiges Finale und erkennt gleiche Belege am Inhalt. Die Kampf-Vorschau sagt ehrlich, was ein Treffer bringt, und die Meldungen sind ruhiger.
- Keine blauen Knöpfe mehr. „Feierabend“ ist jetzt dunkles Eichenholz, Stempel-Knöpfe sind siegelrot. Das Ablagefeld am Handy ist ordentlich ausgerichtet.

### Neu in 1.7.2
- **Rechnungen von früher nachtragen geht glatter:** Eine Lücke in den Nummern ist erlaubt („Stimmt, trotzdem nachtragen“), Beträge wie „600,00 EUR“ oder „600,- €“ werden so angenommen, wie sie auf dem Papier stehen, und Fehlermeldungen stehen direkt am Feld. Eine schon nachgetragene Rechnung wird erkannt („Zur Rechnung“). Eine eigene alte Rechnung nur mit Briefkopf erkennt FRITS unter Ausgaben und fragt nach.
- Einrichtung kürzer, am Handy keine Hinweise mehr auf Tastenkürzel oder den Finder.
- Fritte und Steuer-Quest: gemalte Bilder für Laden, Gewürze, Zimmer und Truhen, ehrliche Truhen, kürzerer Laden am Handy, ein sinnvolles Gericht ist vorgewählt, Zurücklegen und Neuwerfen bringen keine geschenkten Körner mehr.

### Neu in 1.7.1
- **Schnellerer Start mit Ladebild:** Beim Doppelklick erscheint sofort Fritte mit „FRITS startet …“, und das Fenster wird von selbst zu FRITS. Das Sprachmodell zum Schwärzen lädt danach im Hintergrund, so geht das erste „Jetzt lesen“ schneller.
- Heute nennt den nächsten Schritt in der Steuer-Quest. Fritte steht am Computer unter „Mehr“.

### Neu in 1.7.0
- **Einfacher:** Oben stehen nur noch fünf Orte (Heute, Rechnungen, Ausgaben, Kunden, Steuer-Quest). Entwürfe und Angebote sind Reiter unter Rechnungen, EÜR und Umsatzsteuer Reiter unter Ausgaben. Auf Heute sagt „Als Nächstes“, was jetzt dran ist. Die Einstellungen sind in Fächer sortiert.
- **Ein Chat** statt Notfall-Chat und freiem Chat. Klappt das Schwärzen nicht ganz, wartet die Datei schon im Chat, und nichts geht raus. Die KI-Kosten stehen im Chat und unter Einstellungen › KI, bei lokaler KI mit „0 €“.
- **Rechnungen von früher** einfach hineinziehen: FRITS liest sie auf deinem Mac ohne KI und fragt „Schon bezahlt?“. Eine eigene alte Rechnung, die auf Heute landet, wird erkannt und nicht mehr zur Ausgabe.
- **Verwerfen** auf den Prüf-Bildschirmen.
- **Steuer-Quest aufgeräumt:** eine schlanke Insel mit einem Ablagefeld, die Provinz zeigt einen Zettel nach dem anderen, und deine FRITS-Rechnungen kommen von selbst mit. Der Hafenmarkt ist ein Marktstand.
- **Spiel:** Fritte und die Steuer-Quest teilen sich einen Beutel mit Salzkörnern. Der Feierabend beginnt mit einem Abspann. Statt Emojis gibt es gemalte Symbole.
- Rechnungen: „z. Hd.“ steht unter der Firma, und der eigene Name wird nie Kunde.

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

## FRITS für den Mac

1. **FRITS-macOS.zip** unten herunterladen und doppelklicken. Es entsteht ein Ordner **FRITS** mit FRITS.app, Start-FRITS.command und LIESMICH.txt.
2. Den Ordner dorthin legen, wo er bleiben soll (z. B. Programme oder USB-Stick).
3. FRITS.app doppelklicken. Beim ersten Mal blockiert der Mac die App, weil sie nicht aus dem App Store kommt:
   - Warnung schließen.
   - **Systemeinstellungen → Datenschutz & Sicherheit** öffnen und ganz nach unten scrollen.
   - Bei „FRITS wurde blockiert“ auf **Trotzdem öffnen** klicken und mit dem Passwort bestätigen.

Sagt der Mac stattdessen, die App sei „beschädigt“: Terminal öffnen, `xattr -dr com.apple.quarantine ` tippen (mit Leerzeichen am Ende), den FRITS-Ordner ins Fenster ziehen und Enter drücken.

Läuft ohne Internet, auf Macs mit Apple-Chip und Intel, ab macOS 13. Deine Daten liegen in `Dokumente/FRITS`, nicht im App-Ordner.

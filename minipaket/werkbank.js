/* Die Werkbank: Text rein, geschwärzt raus, Namen zurück. Alles bleibt in diesem Fenster.
   Nichts wird gespeichert, auch nicht im Browser: Neu laden heißt neu anfangen. */
(function () {
  "use strict";
  var S = window.Schwaerzen;
  var bank = document.querySelector("[data-werkbank]");
  if (!S || !bank) return;

  function $(sel) { return bank.querySelector(sel); }
  var feld = $("[data-text]"), woerterFeld = $("[data-woerter]"), aufgabeFeld = $("[data-aufgabe]");
  var antwortFeld = $("[data-antwort]"), ergebnis = $("[data-ergebnis]"), hinweise = $("[data-hinweise]");
  var stand = { namen: [], aufdecken: [] };
  var letztes = null;

  var BEISPIEL = [
    "Sehr geehrte Frau Albrecht,",
    "wie besprochen schicke ich Ihnen die Nebenkostenabrechnung 2025 für Ihre Wohnung im Gartenweg 12, 50667 Köln.",
    "Die Nachzahlung beträgt 214,60 €. Bitte überweisen Sie bis 30.11.2026 auf DE89 3704 0044 0532 0130 00.",
    "Rückfragen gern an hausverwaltung.koenig@example.de oder 0221 9988776.",
    "Mit freundlichen Grüßen",
    "Petra König, Hausverwaltung König",
    "Aktenzeichen: NK-2025-0815",
  ].join("\n");

  function zeilen(text) {
    return text.split(/\n/).map(function (z) { return z.trim(); }).filter(Boolean);
  }
  function enthaelt(liste, wort) {
    return liste.some(function (x) { return x.toLowerCase() === wort.toLowerCase(); });
  }
  function ohne(liste, wort) {
    return liste.filter(function (x) { return x.toLowerCase() !== wort.toLowerCase(); });
  }

  function neu() {
    letztes = S.schwaerzen(feld.value, { woerter: woerterFeld.value, namen: stand.namen, aufdecken: stand.aufdecken });
    malen();
    zurueckSetzen();
  }

  function malen() {
    var r = letztes;
    ergebnis.textContent = "";
    r.teile.forEach(function (t) {
      if (t.platzhalter) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "balken" + (t.schluessel.indexOf("GEHEIM:") === 0 ? " von-hand" : "");
        b.textContent = t.platzhalter;
        b.title = "Steht für: " + t.original + " · tippen zum Aufdecken";
        b.setAttribute("aria-label", t.platzhalter + ", steht für " + t.original + ". Tippen zum Aufdecken.");
        b.setAttribute("data-schluessel", t.schluessel);
        b.setAttribute("data-original", t.original);
        ergebnis.appendChild(b);
        return;
      }
      // Wörter zum Antippen, alles andere bleibt einfacher Text
      var re = /[A-Za-zÄÖÜäöüßà-ÿ0-9][A-Za-zÄÖÜäöüßà-ÿ0-9@._+\-]*[A-Za-zÄÖÜäöüßà-ÿ0-9]|[A-Za-zÄÖÜäöüßà-ÿ0-9]/g;
      var pos = 0, m;
      while ((m = re.exec(t.text)) !== null) {
        if (m.index > pos) ergebnis.appendChild(document.createTextNode(t.text.slice(pos, m.index)));
        var w = document.createElement("span");
        w.className = "w" + (r.hinweise.indexOf(m[0]) >= 0 ? " verdacht" : "");
        w.textContent = m[0];
        ergebnis.appendChild(w);
        pos = m.index + m[0].length;
      }
      if (pos < t.text.length) ergebnis.appendChild(document.createTextNode(t.text.slice(pos)));
    });

    var anzahl = Object.keys(r.zuordnung).length;
    var stellen = r.teile.filter(function (t) { return t.platzhalter; }).length;
    $("[data-zaehler]").textContent = !feld.value.trim() ? "" : anzahl
      ? "🖤 " + stellen + (stellen === 1 ? " Stelle" : " Stellen") + " geschwärzt, " + anzahl + (anzahl === 1 ? " Platzhalter." : " verschiedene Platzhalter.")
      : "Nichts gefunden. Tipp Wörter an, die weg sollen.";

    var tbody = $("[data-zuordnung]");
    tbody.textContent = "";
    Object.keys(r.zuordnung).forEach(function (p) {
      var e = r.zuordnung[p];
      var tr = document.createElement("tr");
      [p, e.formen.join(" · "), S.ARTEN[e.art] || e.art].forEach(function (inhalt, i) {
        var td = document.createElement("td");
        if (i === 0) { var c = document.createElement("code"); c.textContent = inhalt; td.appendChild(c); }
        else td.textContent = inhalt;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });

    hinweiseMalen(r);
    $("[data-nur-text]").textContent = r.geschwaerzt;
    $("[data-prompt]").textContent = S.prompt(r.geschwaerzt, aufgabeFeld.value);
  }

  function hinweiseMalen(r) {
    hinweise.textContent = "";
    var eintraege = [];
    r.hinweise.forEach(function (wort) {
      eintraege.push({ text: "„" + wort + "“ steht noch allein im Text. Ein Nachname?", knopf: "Auch schwärzen", tun: function () { stand.namen.push(wort); neu(); }, verdacht: true });
    });
    stand.namen.forEach(function (wort) {
      eintraege.push({ text: "„" + wort + "“ wird als Name geschwärzt.", knopf: "Rückgängig", tun: function () { stand.namen = ohne(stand.namen, wort); neu(); } });
    });
    stand.aufdecken.forEach(function (schluessel) {
      var wert = schluessel.slice(schluessel.indexOf(":") + 1);
      eintraege.push({ text: "Aufgedeckt: „" + wert + "“", knopf: "Wieder schwärzen", tun: function () {
        stand.aufdecken = stand.aufdecken.filter(function (k) { return k !== schluessel; }); neu();
      } });
    });
    if (!eintraege.length) return;
    var ul = document.createElement("ul");
    ul.className = "hinweis-liste";
    eintraege.forEach(function (e) {
      var li = document.createElement("li");
      var span = document.createElement("span");
      span.textContent = (e.verdacht ? "⚠️ " : "") + e.text;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "tiny" + (e.verdacht ? " primary" : "");
      b.textContent = e.knopf;
      b.addEventListener("click", e.tun);
      li.appendChild(span);
      li.appendChild(b);
      ul.appendChild(li);
    });
    hinweise.appendChild(ul);
  }

  function zurueckSetzen() {
    $("[data-zurueck]").textContent = antwortFeld.value.trim()
      ? S.zuruecksetzen(antwortFeld.value, letztes ? letztes.zuordnung : {})
      : "";
  }

  // Antippen: ein Wort schwärzen, einen Balken aufdecken
  ergebnis.addEventListener("click", function (e) {
    var balken = e.target.closest(".balken");
    if (balken) {
      var k = balken.getAttribute("data-schluessel");
      var wert = k.slice(k.indexOf(":") + 1);
      if (k.indexOf("GEHEIM:") === 0) {
        woerterFeld.value = zeilen(woerterFeld.value).filter(function (z) { return z.toLowerCase() !== wert; }).join("\n");
      } else if (k.indexOf("PERSON:") === 0 && enthaelt(stand.namen, wert)) {
        stand.namen = ohne(stand.namen, wert);
      } else {
        stand.aufdecken.push(k);
      }
      neu();
      return;
    }
    var w = e.target.closest(".w");
    if (!w) return;
    var wort = w.textContent;
    if (letztes && letztes.hinweise.indexOf(wort) >= 0) {
      stand.namen.push(wort);
    } else if (!enthaelt(zeilen(woerterFeld.value), wort)) {
      woerterFeld.value = zeilen(woerterFeld.value).concat([wort]).join("\n");
    }
    neu();
  });

  var uhr = null;
  function bald() { clearTimeout(uhr); uhr = setTimeout(neu, 150); }
  feld.addEventListener("input", bald);
  woerterFeld.addEventListener("input", bald);
  aufgabeFeld.addEventListener("input", function () {
    if (letztes) $("[data-prompt]").textContent = S.prompt(letztes.geschwaerzt, aufgabeFeld.value);
  });
  antwortFeld.addEventListener("input", zurueckSetzen);

  $("[data-beispiel]").addEventListener("click", function () {
    feld.value = BEISPIEL;
    stand = { namen: [], aufdecken: [] };
    neu();
  });
  $("[data-leeren]").addEventListener("click", function () {
    feld.value = woerterFeld.value = aufgabeFeld.value = antwortFeld.value = "";
    stand = { namen: [], aufdecken: [] };
    $("[data-datei-meldung]").textContent = "";
    neu();
    feld.focus();
  });

  // ---------- Dateien: ziehen oder auswählen, gelesen wird nur hier ----------
  var meldung = $("[data-datei-meldung]");
  function lesen(datei) {
    if (!datei) return;
    var name = datei.name || "Datei";
    if (/\.(pdf|docx?|odt|pages|jpe?g|png|heic|gif|webp|zip)$/i.test(name)) {
      meldung.textContent = "„" + name + "“ kann die Werkbank nicht lesen. Öffne die Datei, markiere den Text, kopiere ihn und füge ihn oben ein.";
      return;
    }
    if (datei.size > 2000000) {
      meldung.textContent = "„" + name + "“ ist zu groß. Kopiere nur den Teil, den du brauchst.";
      return;
    }
    var leser = new FileReader();
    leser.onload = function () {
      var text = String(leser.result || "");
      if (/\.html?$/i.test(name)) {
        // Nur der Text, ohne Bilder oder Skripte zu laden
        var doc = new DOMParser().parseFromString(text, "text/html");
        text = doc.body ? doc.body.innerText || doc.body.textContent : text;
      }
      feld.value = text;
      stand = { namen: [], aufdecken: [] };
      meldung.textContent = "✓ „" + name + "“ gelesen" + (text.length > S.MAX_TEXT
        ? ". Sie ist lang, darum nimmt die Werkbank nur die ersten " + S.MAX_TEXT.toLocaleString("de-DE") + " Zeichen."
        : ".");
      neu();
    };
    leser.onerror = function () { meldung.textContent = "„" + name + "“ ließ sich nicht lesen."; };
    leser.readAsText(datei);
  }
  var ablage = $("[data-ablage]");
  $("[data-datei]").addEventListener("change", function (e) { lesen(e.target.files[0]); e.target.value = ""; });
  [ablage, feld].forEach(function (ziel) {
    ziel.addEventListener("dragover", function (e) { e.preventDefault(); ablage.classList.add("drueber"); });
    ziel.addEventListener("dragleave", function () { ablage.classList.remove("drueber"); });
    ziel.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return; // gezogener Text: der Browser fügt ihn selbst ein
      e.preventDefault();
      e.stopPropagation();
      ablage.classList.remove("drueber");
      lesen(e.dataTransfer.files[0]);
    });
  });
  // Eine Datei, die neben das Feld fällt, soll nicht die Seite ersetzen
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); lesen(e.dataTransfer.files[0]); }
  });

  neu();
})();

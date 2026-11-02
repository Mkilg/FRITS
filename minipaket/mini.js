/* Erst schwärzen, dann fragen: alles zum Anklicken. Ohne JavaScript ist jeder Happen und jede
   Antwort einfach sichtbar, es geht also nichts verloren.
   Gespeichert wird nur, welche Happen du gesehen und welche Übungen du gelöst hast, und das nur
   in diesem Browser (localStorage). Nie ein Text, nie ein Name. */
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  var SPEICHER = "erst-schwaerzen:";

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function lies(k, standard) {
    try { var v = window.localStorage.getItem(SPEICHER + k); return v ? JSON.parse(v) : standard; } catch (e) { return standard; }
  }
  function schreib(k, wert) {
    try { window.localStorage.setItem(SPEICHER + k, JSON.stringify(wert)); } catch (e) { /* privates Fenster: dann eben ohne */ }
  }
  function ruhig() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // ---------- Kopieren: geht auch per Doppelklick von einem USB-Stick ----------
  function kopieren(text, knopf) {
    function geschafft() {
      if (!knopf) return;
      var vorher = knopf.getAttribute("data-text") || knopf.textContent;
      knopf.setAttribute("data-text", vorher);
      knopf.textContent = "✓ Kopiert";
      knopf.classList.add("kopiert");
      setTimeout(function () { knopf.textContent = vorher; knopf.classList.remove("kopiert"); }, 1800);
    }
    function vonHand() {
      var feld = document.createElement("textarea");
      feld.value = text;
      feld.setAttribute("readonly", "");
      feld.style.position = "fixed";
      feld.style.opacity = "0";
      document.body.appendChild(feld);
      feld.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(feld);
      if (ok) geschafft();
      else if (knopf) knopf.textContent = "Bitte markieren und mit ⌘C kopieren";
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(geschafft, vonHand);
    } else {
      vonHand();
    }
  }
  document.addEventListener("click", function (e) {
    var knopf = e.target.closest && e.target.closest("[data-kopieren]");
    if (!knopf) return;
    var quelle = document.querySelector(knopf.getAttribute("data-kopieren"));
    if (!quelle) return;
    kopieren("value" in quelle && quelle.tagName !== "PRE" ? quelle.value : quelle.textContent, knopf);
  });
  each(document.querySelectorAll("[data-drucken]"), function (b) {
    b.addEventListener("click", function () { window.print(); });
  });
  // Alle Prompts als eine Textdatei: entsteht hier im Browser, nichts wird geladen
  each(document.querySelectorAll("[data-alle-speichern]"), function (b) {
    b.addEventListener("click", function () {
      var teile = [];
      each(document.querySelectorAll(".kopierbox"), function (box) {
        var titel = box.querySelector("figcaption strong");
        var pre = box.querySelector("pre");
        if (!titel || !pre) return;
        teile.push(titel.textContent + "\n" + new Array(titel.textContent.length + 1).join("=") + "\n\n" + pre.textContent);
      });
      var datei = new Blob([teile.join("\n\n\n")], { type: "text/plain;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(datei);
      a.download = "Prompts - Erst schwaerzen, dann fragen.txt";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    });
  });
  window.Mini = { kopieren: kopieren, each: each };

  // ---------- Übungen: ein Happen gilt als gelöst, wenn jede Aufgabe darin geschafft ist ----------
  var beimLoesen = function () {};
  var register = {};
  function aufgabe(el) {
    var happen = el.closest("[data-uebung]");
    if (!happen) return function () {};
    var liste = register[happen.id] || (register[happen.id] = []);
    var a = { fertig: false };
    liste.push(a);
    return function () {
      if (a.fertig) return;
      a.fertig = true;
      if (liste.every(function (x) { return x.fertig; })) beimLoesen(happen.id);
    };
  }

  // ---------- Tippen und die passende Tafel sehen (Treppe, Stationen …) ----------
  each(document.querySelectorAll("[data-auswahl]"), function (gruppe) {
    var knoepfe = gruppe.querySelectorAll("[data-zeige]");
    var tafeln = gruppe.querySelectorAll("[data-tafel]");
    var schluessel = Array.prototype.map.call(knoepfe, function (b) { return b.getAttribute("data-zeige"); });
    function zeige(k) {
      each(knoepfe, function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-zeige") === k ? "true" : "false"); });
      each(tafeln, function (t) { t.classList.toggle("offen", t.getAttribute("data-tafel") === k); });
      gruppe.setAttribute("data-gewaehlt", k);
    }
    each(knoepfe, function (b) { b.addEventListener("click", function () { zeige(b.getAttribute("data-zeige")); }); });
    var start = gruppe.getAttribute("data-start");
    if (schluessel.length) zeige(schluessel.indexOf(start) >= 0 ? start : schluessel[0]);
    var naechste = gruppe.querySelector("[data-naechste]");
    if (naechste) {
      naechste.addEventListener("click", function () {
        var i = schluessel.indexOf(gruppe.getAttribute("data-gewaehlt"));
        zeige(schluessel[(i + 1) % schluessel.length]);
        naechste.textContent = i + 1 === schluessel.length - 1 ? "Nochmal von vorn ↺" : "Nächste Station →";
      });
    }
  });

  // ---------- Quiz: der erste Versuch zählt, nochmal probieren ist immer erlaubt ----------
  each(document.querySelectorAll("[data-quiz]"), function (quiz) {
    var fragen = quiz.querySelectorAll("[data-frage]");
    var stand = quiz.querySelector("[data-quiz-stand]");
    var richtig = 0, beantwortet = 0, gekonnt = 0;
    var fertig = aufgabe(quiz);
    each(fragen, function (frage) {
      var erster = true, geschafft = false;
      var meldung = frage.querySelector(".rueckmeldung");
      var antworten = frage.querySelectorAll("[data-antwort]");
      each(antworten, function (b) {
        b.addEventListener("click", function () {
          var ok = b.hasAttribute("data-richtig");
          each(antworten, function (x) { x.classList.remove("gewaehlt-richtig", "gewaehlt-falsch"); });
          b.classList.add(ok ? "gewaehlt-richtig" : "gewaehlt-falsch");
          meldung.textContent = (ok ? "✓ " : "Hmm. ") + b.getAttribute("data-sagt");
          meldung.className = "rueckmeldung " + (ok ? "gut-text" : "schlecht-text");
          if (ok && !geschafft) {
            geschafft = true;
            gekonnt += 1;
            if (gekonnt === fragen.length) fertig();
          }
          if (!erster) return;
          erster = false;
          beantwortet += 1;
          if (ok) richtig += 1;
          if (beantwortet === fragen.length && stand) {
            stand.textContent = richtig === fragen.length
              ? (fragen.length > 1 ? "🎉 Alle richtig beim ersten Versuch!" : "🎉 Richtig!")
              : richtig + " von " + fragen.length + " beim ersten Versuch richtig. Gut gemacht!";
            stand.classList.add("fertig");
          }
        });
      });
    });
  });

  // ---------- Das Schwärz-Spiel ----------
  each(document.querySelectorAll("[data-schwaerzspiel]"), function (spiel) {
    var woerter = spiel.querySelectorAll("[data-wort]");
    var ergebnis = spiel.querySelector("[data-ergebnis]");
    var fertig = aufgabe(spiel);
    var geschenkt = false;
    each(woerter, function (w) {
      w.addEventListener("click", function () {
        w.setAttribute("aria-pressed", w.getAttribute("aria-pressed") === "true" ? "false" : "true");
        w.classList.remove("uebersehen", "zuviel", "treffer");
        geschenkt = false;
      });
    });
    function pruefen() {
      var geheim = 0, gefunden = 0, uebersehen = [], zuviel = [];
      each(woerter, function (w) {
        var schwarz = w.getAttribute("aria-pressed") === "true";
        var soll = w.hasAttribute("data-geheim");
        w.classList.remove("uebersehen", "zuviel", "treffer");
        if (soll) geheim += 1;
        if (soll && schwarz) { gefunden += 1; w.classList.add("treffer"); }
        if (soll && !schwarz) { uebersehen.push(w); w.classList.add("uebersehen"); }
        if (!soll && schwarz) { zuviel.push(w); w.classList.add("zuviel"); }
      });
      ergebnis.textContent = "";
      var kopf = document.createElement("p");
      kopf.className = "ergebnis-kopf";
      if (!uebersehen.length && !zuviel.length) {
        kopf.textContent = "🎉 Perfekt! Alle " + geheim + " erwischt, und nichts zu viel.";
        if (!geschenkt) fertig();
      } else {
        kopf.textContent = "Du hast " + gefunden + " von " + geheim + " erwischt.";
      }
      ergebnis.appendChild(kopf);
      function liste(titel, items) {
        if (!items.length) return;
        var p = document.createElement("p");
        p.textContent = titel;
        var ul = document.createElement("ul");
        ul.className = "kurz";
        items.forEach(function (w) {
          var li = document.createElement("li");
          var b = document.createElement("strong");
          b.textContent = w.textContent;
          li.appendChild(b);
          li.appendChild(document.createTextNode(": " + w.getAttribute("data-warum")));
          ul.appendChild(li);
        });
        ergebnis.appendChild(p);
        ergebnis.appendChild(ul);
      }
      liste("Noch zu sehen (rot umrandet):", uebersehen);
      liste("Das darf bleiben (gelb umrandet):", zuviel);
    }
    spiel.querySelector("[data-pruefen]").addEventListener("click", pruefen);
    spiel.querySelector("[data-aufloesen]").addEventListener("click", function () {
      each(woerter, function (w) { w.setAttribute("aria-pressed", w.hasAttribute("data-geheim") ? "true" : "false"); });
      geschenkt = true;
      pruefen();
      ergebnis.firstChild.textContent = "So wäre es richtig. Tipp die Wörter jetzt selbst an, dann zählt die Übung.";
      each(woerter, function (w) { w.setAttribute("aria-pressed", "false"); w.classList.remove("treffer"); });
      geschenkt = true;
    });
  });

  // ---------- Zeile für Zeile abspielen ----------
  each(document.querySelectorAll("[data-abspielen]"), function (box) {
    var spalten = box.querySelectorAll(".protokoll");
    var knopf = box.querySelector("[data-abspielen-start]");
    var uhren = [];
    each(spalten, function (ol) { each(ol.children, function (li) { li.classList.add("wartet"); }); });
    knopf.addEventListener("click", function () {
      uhren.forEach(clearTimeout);
      uhren = [];
      var meiste = 0;
      each(spalten, function (ol) {
        meiste = Math.max(meiste, ol.children.length);
        each(ol.children, function (li) { li.classList.add("wartet"); });
      });
      for (var i = 0; i < meiste; i += 1) {
        (function (i) {
          uhren.push(setTimeout(function () {
            each(spalten, function (ol) { if (ol.children[i]) ol.children[i].classList.remove("wartet"); });
          }, ruhig() ? 0 : 250 + i * 900));
        })(i);
      }
      knopf.textContent = "↺ Nochmal abspielen";
    });
  });

  // ---------- Schalter ----------
  each(document.querySelectorAll(".einstellung-mock"), function (mock) {
    var schalter = mock.querySelectorAll("[data-schalter]");
    var fertig = aufgabe(mock);
    function ziel(s) { return s.classList.contains("umgekehrt") ? "false" : "true"; }
    each(schalter, function (s) {
      var zeile = s.closest(".mock-zeile");
      var lob = zeile && zeile.nextElementSibling && zeile.nextElementSibling.classList.contains("schalter-lob") ? zeile.nextElementSibling : null;
      s.addEventListener("click", function () {
        var jetzt = s.getAttribute("aria-pressed") === "true" ? "false" : "true";
        s.setAttribute("aria-pressed", jetzt);
        if (lob) lob.hidden = jetzt !== ziel(s);
        if (Array.prototype.every.call(schalter, function (x) { return x.getAttribute("aria-pressed") === ziel(x); })) fertig();
      });
    });
  });

  // ---------- Die Schritte in die richtige Reihenfolge ----------
  each(document.querySelectorAll("[data-reihenfolge]"), function (box) {
    var knoepfe = box.querySelectorAll(".reihen-knopf");
    var meldung = box.querySelector("[data-reihen-meldung]");
    var loesung = box.querySelector("[data-reihen-loesung]");
    var naechster = 1, fehler = 0;
    var fertig = aufgabe(box);
    each(knoepfe, function (b) {
      b.addEventListener("click", function () {
        if (b.disabled) return;
        if (Number(b.getAttribute("data-pos")) === naechster) {
          b.disabled = true;
          b.classList.add("richtig");
          b.classList.remove("tipp");
          b.querySelector(".reihen-nr").textContent = naechster;
          naechster += 1;
          fehler = 0;
          meldung.className = "rueckmeldung gut-text";
          if (naechster > knoepfe.length) {
            meldung.textContent = "🎉 Genau so! Das ist das Rezept für jeden Papierkram.";
            loesung.classList.add("offen");
            fertig();
          } else {
            meldung.textContent = "✓ Richtig. Und dann?";
          }
        } else {
          fehler += 1;
          b.classList.remove("wackeln");
          void b.offsetWidth; // das kleine Wackeln neu starten
          b.classList.add("wackeln");
          meldung.className = "rueckmeldung schlecht-text";
          meldung.textContent = "Hmm, nicht ganz. Was kommt als Schritt " + naechster + "?";
          if (fehler >= 2) {
            each(knoepfe, function (x) { x.classList.toggle("tipp", Number(x.getAttribute("data-pos")) === naechster); });
            meldung.textContent += " Ich hab dir einen Tipp markiert.";
          }
        }
      });
    });
  });

  // ---------- Der Spickzettel ----------
  each(document.querySelectorAll("[data-spickzettel]"), function (box) {
    var haken = box.querySelectorAll("input[type=checkbox]");
    var fertigZeile = box.querySelector("[data-spick-fertig]");
    var fertig = aufgabe(box);
    each(haken, function (h) {
      h.addEventListener("change", function () {
        var alle = Array.prototype.every.call(haken, function (x) { return x.checked; });
        fertigZeile.hidden = !alle;
        if (alle) fertig();
      });
    });
  });

  // ---------- Einmal hin und zurück (die kleine Werkbank im Kurs) ----------
  each(document.querySelectorAll("[data-probe]"), function (box) {
    var S = window.Schwaerzen;
    if (!S) return;
    var hin = aufgabe(box), zurueck = aufgabe(box);
    var stand = null;
    var schritt2 = box.querySelector("[data-probe-schritt2]");
    var fertigTeil = box.querySelector("[data-probe-fertig]");
    box.querySelector("[data-probe-schwaerzen]").addEventListener("click", function () {
      stand = S.schwaerzen(box.querySelector("[data-probe-text]").value);
      var p = Object.keys(stand.zuordnung);
      var personen = p.filter(function (x) { return stand.zuordnung[x].art === "PERSON"; });
      var iban = p.filter(function (x) { return stand.zuordnung[x].art === "IBAN"; })[0];
      box.querySelector("[data-probe-ergebnis]").textContent = stand.geschwaerzt;
      box.querySelector("[data-probe-tipp]").textContent = p.length
        ? "✓ " + p.length + (p.length === 1 ? " Angabe" : " Angaben") + " geschwärzt. Die Liste dazu bleibt hier im Browser."
        : "Hier war nichts zu schwärzen. Schreib mal einen Namen oder eine IBAN hinein.";
      var an = personen.length > 1 ? personen[personen.length - 1] : (personen[0] || "");
      var von = personen.length > 1 ? personen[0] : "";
      box.querySelector("[data-probe-antwort]").textContent =
        (an ? "Liebe " + an + ",\n" : "Hallo,\n") + "danke! Ich habe alles notiert" +
        (iban ? " und schaue nach der Überweisung auf " + iban : "") + ".\nViele Grüße" + (von ? "\n" + von : "");
      schritt2.hidden = false;
      fertigTeil.hidden = true;
      hin();
    });
    box.querySelector("[data-probe-zurueck]").addEventListener("click", function () {
      if (!stand) return;
      fertigTeil.textContent = S.zuruecksetzen(box.querySelector("[data-probe-antwort]").textContent, stand.zuordnung);
      fertigTeil.hidden = false;
      zurueck();
    });
  });

  // ---------- Das Schloss dieser Seite ausprobieren ----------
  each(document.querySelectorAll("[data-schloss-test]"), function (knopf) {
    var ausgabe = knopf.parentNode.querySelector("[data-schloss-ergebnis]");
    var gesperrt = false;
    document.addEventListener("securitypolicyviolation", function (e) {
      if (String(e.blockedURI).indexOf("example.com") >= 0) gesperrt = true;
    });
    knopf.addEventListener("click", function () {
      gesperrt = false;
      ausgabe.textContent = "…";
      function blockiert() {
        ausgabe.className = "gut-text";
        ausgabe.textContent = gesperrt
          ? "⛔ Blockiert, noch bevor etwas rausging. Das Schloss hält."
          : "⛔ Kam nicht durch. (Bist du offline? Auch gut.)";
      }
      try {
        window.fetch("https://example.com/schloss-test", { mode: "no-cors", cache: "no-store", credentials: "omit" })
          .then(function () {
            ausgabe.className = "schlecht-text";
            ausgabe.textContent = "😬 Ging durch. Dieser Browser kennt das Schloss nicht. Nimm einen aktuellen.";
          }, function () { setTimeout(blockiert, 50); });
      } catch (e) {
        blockiert();
      }
    });
  });

  // ---------- Der Kurs: ein Happen nach dem anderen ----------
  var kurs = document.querySelector("[data-kurs]");
  if (!kurs) return;

  var etappen = Array.prototype.map.call(kurs.querySelectorAll("[data-etappe]"), function (e, i) {
    return {
      el: e, nr: i + 1, slug: e.getAttribute("data-etappe"), titel: e.getAttribute("data-titel"),
      bild: e.getAttribute("data-bild"), happen: Array.prototype.slice.call(e.querySelectorAll(".happen")),
    };
  });
  var reihe = [];
  etappen.forEach(function (e) { e.happen.forEach(function (h) { reihe.push({ el: h, etappe: e }); }); });
  var gesehen = lies("gesehen", []);
  var geloest = lies("geloest", []);

  function istGeloest(id) { return geloest.indexOf(id) >= 0; }
  function etappeStand(e) {
    var ids = e.happen.map(function (h) { return h.id; });
    var uebungen = e.happen.filter(function (h) { return h.hasAttribute("data-uebung"); }).map(function (h) { return h.id; });
    return {
      gesehen: ids.filter(function (id) { return gesehen.indexOf(id) >= 0; }).length,
      alle: ids.length,
      geloest: uebungen.filter(istGeloest).length,
      uebungen: uebungen.length,
      erster: ids.filter(function (id) { return gesehen.indexOf(id) < 0; })[0] || ids[0],
    };
  }
  function geschafft(e) {
    var s = etappeStand(e);
    return s.gesehen === s.alle && s.geloest === s.uebungen;
  }

  beimLoesen = function (id) {
    if (!istGeloest(id)) { geloest.push(id); schreib("geloest", geloest); }
    var marke = document.querySelector("#" + id + " .uebung-marke");
    if (marke) { marke.textContent = "✓ Übung gelöst"; marke.classList.add("geloest"); }
    punkteMalen();
  };

  // Kopf mit Punkten über jedem Happen, Weiter-Knöpfe darunter
  reihe.forEach(function (eintrag, i) {
    var h = eintrag.el, e = eintrag.etappe;
    var kopf = document.createElement("div");
    kopf.className = "etappe-kopf";
    kopf.innerHTML = '<img alt="" src="' + e.bild + '"><span class="etappe-name"></span><ol class="punkte"></ol><a class="zur-karte" href="#karte">Zur Kurs-Karte</a>';
    kopf.querySelector(".etappe-name").textContent = "Etappe " + e.nr + " von " + etappen.length + ": " + e.titel;
    var punkte = kopf.querySelector(".punkte");
    e.happen.forEach(function (x, j) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#" + x.id;
      a.setAttribute("data-punkt", x.id);
      a.setAttribute("aria-label", "Happen " + (j + 1) + " von " + e.happen.length);
      li.appendChild(a);
      punkte.appendChild(li);
    });
    h.insertBefore(kopf, h.firstChild);

    if (h.hasAttribute("data-uebung")) {
      var marke = document.createElement("p");
      marke.className = "uebung-marke";
      marke.textContent = istGeloest(h.id) ? "✓ Übung gelöst" : "✎ Übung";
      if (istGeloest(h.id)) marke.classList.add("geloest");
      var karte = h.querySelector(".card");
      karte.insertBefore(marke, karte.firstChild);
    }

    var weiter = document.createElement("div");
    weiter.className = "weiter-reihe";
    var vorher = reihe[i - 1], nachher = reihe[i + 1];
    if (vorher) weiter.innerHTML += '<a class="button quiet" href="#' + vorher.el.id + '">← Zurück</a>';
    weiter.innerHTML += '<span class="platz"></span>';
    if (nachher) {
      var neueEtappe = nachher.etappe !== e;
      weiter.innerHTML += '<a class="button primary" href="#' + nachher.el.id + '">' +
        (neueEtappe ? "Weiter zu Etappe " + nachher.etappe.nr + " →" : "Weiter →") + "</a>";
    }
    h.appendChild(weiter);
  });

  function punkteMalen(aktiv) {
    each(kurs.querySelectorAll("[data-punkt]"), function (a) {
      var id = a.getAttribute("data-punkt");
      a.classList.toggle("gesehen", gesehen.indexOf(id) >= 0);
      a.classList.toggle("jetzt", id === aktiv);
    });
    each(kurs.querySelectorAll("[data-halt]"), function (halt) {
      var e = etappen.filter(function (x) { return x.slug === halt.getAttribute("data-halt"); })[0];
      if (!e) return;
      var s = etappeStand(e);
      var fertig = geschafft(e);
      halt.classList.toggle("geschafft", fertig);
      halt.classList.toggle("angefangen", !fertig && s.gesehen > 0);
      halt.querySelector("a").href = "#" + (fertig ? e.happen[0].id : s.erster);
      var text = "";
      if (fertig) text = "✓ geschafft";
      else if (s.gesehen > 0) text = s.gesehen + " von " + s.alle + (s.uebungen ? " · Übungen " + s.geloest + "/" + s.uebungen : "");
      halt.querySelector(".halt-stand").textContent = text;
      halt.querySelector(".halt-nr").textContent = fertig ? "✓" : String(e.nr);
    });
  }

  function urkundeFuellen() {
    var offen = etappen.filter(function (e) { return !geschafft(e); });
    var hinweis = kurs.querySelector("[data-urkunde-offen]");
    var titel = kurs.querySelector("[data-urkunde] h2");
    var stempel = kurs.querySelector("[data-urkunde] .stempel");
    kurs.querySelector("[data-urkunde-datum]").textContent =
      new Date().toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric" });
    titel.textContent = offen.length ? "Fast geschafft!" : "Geschafft!";
    stempel.hidden = offen.length > 0;
    hinweis.hidden = !offen.length;
    hinweis.textContent = "";
    if (offen.length) {
      hinweis.appendChild(document.createTextNode("Für den Stempel fehlt noch etwas in: "));
      offen.forEach(function (e, i) {
        var a = document.createElement("a");
        a.href = "#" + etappeStand(e).erster;
        a.textContent = "Etappe " + e.nr;
        if (i) hinweis.appendChild(document.createTextNode(", "));
        hinweis.appendChild(a);
      });
    }
  }

  var teile = kurs.querySelectorAll(".kurs-teil");
  function zeige(id, scrollen) {
    var ziel = id && document.getElementById(id);
    if (!ziel || !ziel.classList.contains("kurs-teil")) ziel = document.getElementById("karte");
    each(teile, function (t) { t.classList.toggle("zu-sehen", t === ziel); });
    if (ziel.classList.contains("happen") && gesehen.indexOf(ziel.id) < 0) {
      gesehen.push(ziel.id);
      schreib("gesehen", gesehen);
    }
    if (ziel.hasAttribute("data-urkunde")) urkundeFuellen();
    punkteMalen(ziel.id);
    var ueberschrift = ziel.querySelector("h1, h2");
    if (scrollen) {
      window.scrollTo(0, 0);
      if (ueberschrift) { ueberschrift.setAttribute("tabindex", "-1"); ueberschrift.focus({ preventScroll: true }); }
    }
  }
  window.addEventListener("hashchange", function () { zeige(location.hash.slice(1), true); });
  zeige(location.hash.slice(1), false);

  var vergessen = kurs.querySelector("[data-vergessen]");
  if (vergessen) {
    vergessen.addEventListener("click", function () {
      gesehen = []; geloest = [];
      schreib("gesehen", gesehen); schreib("geloest", geloest);
      each(kurs.querySelectorAll(".uebung-marke"), function (m) { m.textContent = "✎ Übung"; m.classList.remove("geloest"); });
      punkteMalen("karte");
    });
  }
})();

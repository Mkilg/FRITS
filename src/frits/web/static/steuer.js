/* Steuer-Quest: das Spielgefühl auf dem Kartentisch.
   - Zettel auf Provinzen oder Karten ziehen, Dateien vom Schreibtisch auf Karten ziehen
   - Stempel beim Ablegen (Papers, Please), tickernde Zähler mit steigender Tonhöhe (Balatro)
   - Konfetti und Funken bei Siegeln und beim Sieg
   Alles funktioniert auch ohne JavaScript; hier kommt nur die Würze dazu.
   Töne nur, wenn in FRITS "Klang an" ist. Bewegung nur, wenn der Computer sie nicht abgeschaltet hat.
   Die Quest ist absichtlich immer verspielt, auch wenn FRITS sonst auf "ruhig" steht: sie ist ein Spiel. */
(function () {
  "use strict";

  var tisch = document.querySelector(".sq");
  if (!tisch) return;
  document.body.classList.add("sq-js");
  var ruhig = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var FARBEN = ["#6f9440", "#d9a441", "#c4623a", "#f6ecd2", "#24395c", "#e9bd4f"];

  // ---------- Klang (selbst erzeugt, keine Dateien nötig) ----------
  var ctx = null;
  function klang() {
    if (tisch.getAttribute("data-klang") !== "an") return null;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    } catch (e) { return null; }
  }
  function ton(frequenz, dauer, art, lautstaerke, verzoegerung) {
    var c = klang();
    if (!c) return;
    var start = c.currentTime + (verzoegerung || 0);
    var osc = c.createOscillator();
    var gain = c.createGain();
    osc.type = art || "triangle";
    osc.frequency.setValueAtTime(frequenz, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(lautstaerke || 0.12, start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + dauer);
    osc.connect(gain).connect(c.destination);
    osc.start(start);
    osc.stop(start + dauer + 0.02);
  }
  function stempelTon() {
    var c = klang();
    if (!c) return;
    // Derselbe dumpfe Plopp wie im normalen FRITS (frits.js), dazu ein kurzes Papierrascheln
    if (window.Frits && window.Frits.stampSound) window.Frits.stampSound();
    else ton(120, 0.16, "sine", 0.3);
    var start = c.currentTime;
    var laenge = Math.floor(c.sampleRate * 0.08);
    var puffer = c.createBuffer(1, laenge, c.sampleRate);
    var daten = puffer.getChannelData(0);
    for (var i = 0; i < laenge; i++) daten[i] = (Math.random() * 2 - 1) * (1 - i / laenge);
    var rauschen = c.createBufferSource();
    var g2 = c.createGain();
    g2.gain.value = 0.12;
    rauschen.buffer = puffer;
    rauschen.connect(g2).connect(c.destination);
    rauschen.start(start);
  }
  function fanfare() {
    [523, 659, 784, 1047].forEach(function (f, i) { ton(f, 0.35, "triangle", 0.1, i * 0.11); });
  }

  // ---------- Funken und Konfetti ----------
  function funken(x, y, anzahl) {
    if (ruhig) return;
    for (var i = 0; i < (anzahl || 14); i++) {
      var f = document.createElement("span");
      f.className = "sq-funke";
      var winkel = Math.random() * Math.PI * 2;
      var weite = 40 + Math.random() * 70;
      f.style.left = x + "px";
      f.style.top = y + "px";
      f.style.background = FARBEN[i % FARBEN.length];
      f.style.setProperty("--dx", Math.cos(winkel) * weite + "px");
      f.style.setProperty("--dy", Math.sin(winkel) * weite + "px");
      document.body.appendChild(f);
      setTimeout(f.remove.bind(f), 1000);
    }
  }
  function konfetti() {
    if (ruhig) return;
    for (var i = 0; i < 70; i++) {
      var s = document.createElement("span");
      s.className = "sq-schnipsel";
      s.style.left = Math.random() * 100 + "vw";
      s.style.background = FARBEN[i % FARBEN.length];
      s.style.animationDuration = 2.4 + Math.random() * 2.2 + "s";
      s.style.animationDelay = Math.random() * 0.8 + "s";
      s.style.setProperty("--dx", (Math.random() * 160 - 80) + "px");
      document.body.appendChild(s);
      setTimeout(s.remove.bind(s), 6000);
    }
  }

  // ---------- Tickernde Zähler ----------
  var jahr = (document.querySelector("[data-jahr]") || tisch).getAttribute("data-jahr") || "";
  function merken(name, wert) {
    try { sessionStorage.setItem("sq-" + jahr + "-" + name, String(wert)); } catch (e) { /* egal */ }
  }
  function erinnern(name) {
    try { return sessionStorage.getItem("sq-" + jahr + "-" + name); } catch (e) { return null; }
  }
  var tonhoehe = 0;
  document.querySelectorAll("[data-zaehler]").forEach(function (el) {
    var name = el.getAttribute("data-zaehler");
    var ziel = parseInt(el.textContent, 10);
    if (isNaN(ziel)) return;
    var von = el.hasAttribute("data-von") ? el.getAttribute("data-von") : erinnern(name);
    if (!el.hasAttribute("data-von")) merken(name, ziel);
    if (von === null || parseInt(von, 10) === ziel) return;
    von = parseInt(von, 10);
    if (ruhig) { el.classList.add("sq-pop"); return; }
    var schritte = Math.min(Math.abs(ziel - von), 24);
    var dauer = 650;
    var start = null;
    var letzter = von;
    var steigt = ziel > von;
    el.textContent = von;
    function rahmen(t) {
      if (start === null) start = t;
      var anteil = Math.min(1, (t - start) / dauer);
      var wert = Math.round(von + (ziel - von) * (1 - Math.pow(1 - anteil, 3)));
      if (wert !== letzter) {
        letzter = wert;
        el.textContent = wert;
        if (steigt && name !== "lp" && schritte <= 24) ton(440 * Math.pow(2, tonhoehe++ / 12), 0.07, "square", 0.04);
      }
      if (anteil < 1) requestAnimationFrame(rahmen);
      else {
        el.textContent = ziel;
        el.classList.remove("sq-pop");
        void el.offsetWidth;
        el.classList.add("sq-pop");
      }
    }
    setTimeout(function () { requestAnimationFrame(rahmen); }, 250);
  });

  // ---------- Stempeln beim Abschicken ----------
  var FARBE = { grau: "#7a6e5c", blau: "#4a6896" };
  function stempeln(form, x, y, absender) {
    if (form.getAttribute("data-gestempelt")) return;
    form.setAttribute("data-gestempelt", "1");
    if (absender && absender.name) {
      var versteckt = document.createElement("input");
      versteckt.type = "hidden";
      versteckt.name = absender.name;
      versteckt.value = absender.value;
      form.appendChild(versteckt);
    }
    stempelTon();
    if (ruhig) { HTMLFormElement.prototype.submit.call(form); return; }
    var abdruck = document.createElement("div");
    abdruck.className = "sq-stempel-flug";
    abdruck.textContent = form.getAttribute("data-stempel");
    abdruck.style.left = x + "px";
    abdruck.style.top = y + "px";
    var farbe = FARBE[form.getAttribute("data-farbe")];
    if (farbe) abdruck.style.color = farbe;
    tisch.appendChild(abdruck);
    funken(x, y, 16);
    setTimeout(function () { HTMLFormElement.prototype.submit.call(form); }, 480);
  }
  function mitte(el) {
    var r = el.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  }
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute("data-stempel") || e.defaultPrevented) return;
    e.preventDefault();
    var wo = mitte(form.closest(".sq-karte, .sq-papier, .sq-zettel") || form);
    stempeln(form, wo[0], wo[1], e.submitter);
  });

  // Datei ausgewählt: gleich sichern
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (input.type !== "file" || !input.files.length) return;
    var form = input.form;
    if (form && form.hasAttribute("data-auto")) {
      var wo = mitte(form.closest(".sq-karte") || form);
      stempeln(form, wo[0], wo[1]);
    }
  });

  // ---------- Ziehen und Ablegen ----------
  var TYP = "text/x-sq-zettel";
  var gezogen = null;
  document.addEventListener("dragstart", function (e) {
    var zettel = e.target.closest && e.target.closest(".sq-zettel, .sq-handkarte");
    if (!zettel) return;
    gezogen = zettel.getAttribute("data-name");
    // Nur unser eigener Typ, kein text/plain: sonst hält der Einwurf den Zettel für eingefügten Text
    e.dataTransfer.setData(TYP, gezogen);
    e.dataTransfer.effectAllowed = "move";
    zettel.classList.add("zieht");
    ton(660, 0.08, "triangle", 0.06);
  });
  document.addEventListener("dragend", function () {
    gezogen = null;
    document.querySelectorAll(".zieht, .ziel").forEach(function (el) { el.classList.remove("zieht", "ziel"); });
  });

  function zielVon(e) {
    var t = e.target.closest && e.target.closest(".sq-parzelle[data-gebiet], .sq-karte[data-stueck]");
    if (!t) return null;
    var typen = Array.prototype.slice.call(e.dataTransfer.types || []);
    var istZettel = typen.indexOf(TYP) >= 0;
    var istDatei = typen.indexOf("Files") >= 0 && t.hasAttribute("data-stueck");
    return istZettel || istDatei ? t : null;
  }
  document.addEventListener("dragover", function (e) {
    var t = zielVon(e);
    if (!t) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (!t.classList.contains("ziel")) {
      document.querySelectorAll(".ziel").forEach(function (el) { el.classList.remove("ziel"); });
      t.classList.add("ziel");
      ton(880, 0.05, "sine", 0.05);
    }
  });
  document.addEventListener("dragleave", function (e) {
    var t = e.target.closest && e.target.closest(".ziel");
    if (t && !t.contains(e.relatedTarget)) t.classList.remove("ziel");
  });

  var dialog = document.getElementById("sq-ablage");
  function dialogOeffnen(name, gebiet, titel) {
    if (!dialog) return;
    var form = dialog.querySelector("form");
    form.elements.name.value = name;
    dialog.querySelector("[data-feld=name]").textContent = name;
    var passende = 0;
    dialog.querySelectorAll(".sq-wahl button").forEach(function (b) {
      var passt = !gebiet || b.getAttribute("data-gebiet") === gebiet;
      b.hidden = !passt;
      if (passt) passende++;
    });
    if (!passende) dialog.querySelectorAll(".sq-wahl button").forEach(function (b) { b.hidden = false; });
    dialog.querySelector("[data-feld=ort]").textContent = titel
      ? "Welches Fach in " + titel + "? Ich stempel es dann ab."
      : "Wähle das passende Fach. Ich stempel es dann ab.";
    if (dialog.showModal) dialog.showModal();
    else dialog.setAttribute("open", "");
  }
  if (dialog) {
    dialog.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-schliessen") || e.target === dialog) dialog.close();
    });
  }

  document.addEventListener("drop", function (e) {
    var t = zielVon(e);
    if (!t) return;
    e.preventDefault();
    t.classList.remove("ziel");
    var name = e.dataTransfer.getData(TYP);
    // Drachenseite: ein Zettel aus der Hand auf die Karte des Drachen geworfen
    var hand = document.querySelector(".sq-hand form");
    if (name && hand && t.hasAttribute("data-stueck")) {
      var karte = Array.prototype.find.call(hand.querySelectorAll("button[name=zettel]"), function (b) { return b.value === name; });
      // Erst fragen, ob der Zettel wirklich passt (sonst landet ein Urlaubsfoto in der Truhe)
      if (karte) { karte.click(); return; }
    }
    if (name && t.hasAttribute("data-gebiet")) {
      dialogOeffnen(name, t.getAttribute("data-gebiet"), t.getAttribute("data-titel"));
      return;
    }
    if (name && t.hasAttribute("data-stueck") && dialog) {
      var form = dialog.querySelector("form");
      form.elements.name.value = name;
      stempeln(form, e.clientX, e.clientY, { name: "stueck_id", value: t.getAttribute("data-stueck") });
      return;
    }
    if (e.dataTransfer.files.length && t.hasAttribute("data-stueck")) {
      var input = t.querySelector("input[type=file]");
      if (!input) return;
      input.files = e.dataTransfer.files;
      // Im Drachenkampf braucht der Schlag die Aktion "datei"
      stempeln(input.form, e.clientX, e.clientY, input.form.querySelector("[data-datei-waehlen]"));
    }
  });

  // ---------- Momente feiern ----------
  var treffer = tisch.getAttribute("data-treffer");
  if (treffer) {
    var combo = parseInt(tisch.getAttribute("data-combo"), 10) || 1;
    var drache = document.querySelector(".sq-drache-gross");
    var wo = drache ? mitte(drache) : [window.innerWidth / 2, 200];
    stempelTon();
    for (var i = 0; i < Math.min(combo, 6); i++) ton(330 * Math.pow(2, (i * 2 + combo) / 12), 0.12, "square", 0.05, 0.12 + i * 0.07);
    funken(wo[0], wo[1], 10 + combo * 6);
  }
  if (tisch.hasAttribute("data-konfetti")) {
    konfetti();
    fanfare();
  }
  // Der große Moment: Siegel, neues Zeitalter, Orden. Ein Dialog mit Licht, Konfetti und Fanfare,
  // und gleich darunter der nächste Auftrag (so weiß man immer, wie es weitergeht).
  var belohnung = document.querySelector("dialog[data-belohnung]");
  // Nach dem letzten Treffer erst den Drachen falten lassen, dann feiern
  var spaet = belohnung ? parseInt(belohnung.getAttribute("data-spaet"), 10) || 0 : 0;
  if (ruhig) spaet = Math.min(spaet, 300);
  if (spaet && document.querySelector(".finale")) {
    for (var k = 0; k < 5; k++) ton(196 * Math.pow(2, k / 5), 0.18, "sawtooth", 0.035, k * 0.22);
  }
  if (belohnung && belohnung.showModal) setTimeout(feiern, spaet);
  else if (document.querySelector(".sq-meldung.beute, .sq-meldung.treffer")) {
    ton(784, 0.12, "triangle", 0.08);
    ton(1047, 0.2, "triangle", 0.08, 0.09);
  }
  function feiern() {
    belohnung.showModal();
    var los = belohnung.querySelector(".sq-siegelknopf");
    if (los) los.focus();
    setTimeout(function () {
      var s = belohnung.querySelector(".sq-belohnung-siegel");
      if (s) { var w = mitte(s); funken(w[0], w[1], 34); }
      stempelTon();
    }, 380);
    if (!ruhig) setTimeout(konfetti, 250);
    fanfare();
    belohnung.addEventListener("click", function (e) { if (e.target === belohnung) belohnung.close(); });
  }

  // Kleine Meldungen: verschwinden nach ein paar Sekunden von selbst
  Array.prototype.forEach.call(document.querySelectorAll(".sq-meldung"), function (m, i) {
    function weg() { m.classList.add("weg"); setTimeout(function () { m.remove(); }, 420); }
    var knopf = m.querySelector(".sq-weg");
    if (knopf) knopf.addEventListener("click", weg);
    if (!m.classList.contains("warn") && !m.classList.contains("fehler")) setTimeout(weg, 5200 + i * 900);
  });

  // Auftrag "Eingangskorb": zum Korb springen und den ersten Zettel aufklappen
  Array.prototype.forEach.call(document.querySelectorAll('a[href="#sq-korb"]'), function (a) {
    a.addEventListener("click", function (e) {
      var korb = document.getElementById("sq-korb");
      if (!korb) return;
      e.preventDefault();
      korb.scrollIntoView({ behavior: ruhig ? "auto" : "smooth", block: "center" });
      var erster = korb.querySelector("details.sq-zettel");
      if (erster) { erster.open = true; var w = mitte(erster); funken(w[0], w[1], 12); }
    });
  });

  // ---------- Intro: Szene für Szene, wie ein kleiner Film ----------
  var intro = document.querySelector("[data-intro]");
  if (intro && !intro.classList.contains("kurz")) {
    var szenen = intro.querySelectorAll(".sq-szene");
    var punkte = intro.querySelectorAll(".sq-intro-punkte li");
    var jetzt = -1;
    var zeige = function (n) {
      if (n >= szenen.length) return;
      jetzt = n;
      Array.prototype.forEach.call(szenen, function (sz, i) { sz.classList.toggle("jetzt", i === n); });
      Array.prototype.forEach.call(punkte, function (pt, i) { pt.classList.toggle("an", i <= n); });
      intro.classList.toggle("am-ende", n === szenen.length - 1);
      ton(392 * Math.pow(2, n / 6), 0.25, "triangle", 0.06);
      if (n === szenen.length - 1) {
        var knopf = intro.querySelector(".sq-siegelknopf");
        if (knopf) knopf.focus();
      }
    };
    zeige(0);
    var weiter = intro.querySelector("[data-intro-weiter]");
    if (weiter) weiter.addEventListener("click", function () { zeige(jetzt + 1); });
    var skip = intro.querySelector("[data-intro-skip]");
    if (skip) skip.addEventListener("click", function (e) { e.preventDefault(); zeige(szenen.length - 1); });
    intro.addEventListener("click", function (e) {
      if (e.target.closest("a, button")) return;
      zeige(jetzt + 1);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "ArrowRight" || e.key === "Enter") {
        if (jetzt < szenen.length - 1) { e.preventDefault(); zeige(jetzt + 1); }
      }
    });
  }

  // ---------- Fragebogen: Karten antippen, Zähler, und alles sofort merken ----------
  var bogen = document.querySelector("form[data-merken]");
  if (bogen) {
    var gemerkt = bogen.querySelector("[data-gemerkt]");
    var karteAuffrischen = function (karte) {
      var an = false;
      Array.prototype.forEach.call(karte.querySelectorAll("input"), function (inp) {
        if (inp.type === "checkbox" ? inp.checked : parseInt(inp.value, 10) > 0) an = true;
      });
      if (an && !karte.classList.contains("gewaehlt")) {
        karte.classList.add("gewaehlt");
        var w = mitte(karte.querySelector(".sq-wahlbild") || karte);
        funken(w[0], w[1], 10);
        ton(660, 0.12, "triangle", 0.07);
        ton(990, 0.16, "triangle", 0.06, 0.07);
      } else if (!an) {
        karte.classList.remove("gewaehlt");
      }
    };
    Array.prototype.forEach.call(bogen.querySelectorAll("[data-wahlkarte]"), function (karte) {
      karte.addEventListener("click", function (e) {
        var schritt = e.target.closest("[data-schritt]");
        if (schritt) {
          var feld = schritt.parentNode.querySelector("input");
          var wert = Math.max(0, Math.min(20, (parseInt(feld.value, 10) || 0) + parseInt(schritt.getAttribute("data-schritt"), 10)));
          feld.value = wert;
          feld.dispatchEvent(new Event("change", { bubbles: true }));
          return;
        }
        // Tippen auf die Karte selbst schaltet sie an oder aus
        if (e.target.closest("input, label, button")) return;
        var ja = karte.querySelector("input[type=checkbox]");
        var zahl = karte.querySelector("input[type=number]");
        if (ja) { ja.checked = !ja.checked; ja.dispatchEvent(new Event("change", { bubbles: true })); }
        else if (zahl) { zahl.value = parseInt(zahl.value, 10) > 0 ? 0 : 1; zahl.dispatchEvent(new Event("change", { bubbles: true })); }
      });
      karte.addEventListener("change", function () { karteAuffrischen(karte); });
    });

    var uhr = null;
    var merken = function (sofort) {
      var daten = new FormData(bogen);
      daten.append("merken", "1");
      if (sofort && navigator.sendBeacon) { navigator.sendBeacon(bogen.action, daten); return; }
      fetch(bogen.action, { method: "POST", body: daten, credentials: "same-origin", keepalive: true })
        .then(function (r) {
          if (r.ok && gemerkt) {
            gemerkt.textContent = "✓ gemerkt";
            gemerkt.classList.add("da");
            setTimeout(function () { gemerkt.classList.remove("da"); }, 1600);
          }
        })
        .catch(function () {});
    };
    var spaeter = function () { clearTimeout(uhr); uhr = setTimeout(function () { uhr = null; merken(false); }, 500); };
    bogen.addEventListener("input", spaeter);
    bogen.addEventListener("change", spaeter);
    // Tab wechseln oder Seite verlassen: sofort sichern
    var abschicken = false;
    bogen.addEventListener("submit", function () { abschicken = true; });
    var gleichSichern = function () { if (uhr && !abschicken) { clearTimeout(uhr); uhr = null; merken(true); } };
    document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") gleichSichern(); });
    window.addEventListener("pagehide", gleichSichern);
  }
})();

// Wichtige Karten: "Gibt's nicht" fragt einmal mit Begründung nach. Die Frage erscheint als
// Sprechblase über dem Knopf (nichts verrutscht), und der empfohlene Weg ist der Hauptknopf:
// "Dann frag ich lieber". Wer wirklich sicher ist, bestätigt mit dem leisen Knopf daneben.
(function () {
  function schliessen() {
    document.querySelectorAll(".sq-rueckfrage-blase").forEach(function (b) { b.remove(); });
    document.querySelectorAll(".rueckfrage-offen").forEach(function (k) { k.classList.remove("rueckfrage-offen"); });
  }
  function abschicken(knopf) {
    knopf.classList.add("rueckfrage");
    if (knopf.form && knopf.form.requestSubmit) knopf.form.requestSubmit(knopf);
    else knopf.click();
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest(".sq-rueckfrage-blase")) return;
    var knopf = e.target.closest("[data-rueckfrage]");
    if (!knopf) { schliessen(); return; }
    if (knopf.classList.contains("rueckfrage")) return; // schon bestätigt: abschicken lassen
    e.preventDefault();
    if (knopf.classList.contains("rueckfrage-offen")) { schliessen(); return; }
    schliessen();
    knopf.classList.add("rueckfrage-offen");
    var karte = knopf.closest(".sq-karte");
    var fragen = karte && karte.querySelector("button[value=fragen]");
    var blase = document.createElement("div");
    blase.className = "sq-rueckfrage-blase";
    blase.setAttribute("role", "alertdialog");
    var text = document.createElement("p");
    text.textContent = knopf.getAttribute("data-rueckfrage");
    blase.appendChild(text);
    var reihe = document.createElement("div");
    reihe.className = "knoepfe";
    var ja = document.createElement("button");
    ja.type = "button";
    if (fragen) {
      var lieber = document.createElement("button");
      lieber.type = "button";
      lieber.className = "sq-siegelknopf";
      lieber.textContent = "Dann frag ich lieber";
      lieber.addEventListener("click", function () {
        var feld = fragen.form.querySelector("input[name=frage]");
        if (feld && !feld.value) feld.value = knopf.getAttribute("data-rueckfrage-frage") || "";
        schliessen();
        fragen.form.requestSubmit ? fragen.form.requestSubmit(fragen) : fragen.click();
      });
      reihe.appendChild(lieber);
      ja.className = "quiet";
      ja.textContent = "Gibt's wirklich nicht";
    } else {
      ja.className = "sq-siegelknopf";
      ja.textContent = knopf.getAttribute("data-rueckfrage-ja") || "Ja, gibt's wirklich nicht";
      var nein = document.createElement("button");
      nein.type = "button";
      nein.className = "quiet";
      nein.textContent = "Doch nicht";
      nein.addEventListener("click", schliessen);
      reihe.appendChild(ja);
      reihe.appendChild(nein);
      // Ein Zettel aus der Hand, der nirgends passt: beiseitelegen (nichts wird gelöscht)
      var beiseite = knopf.form && knopf.form.getAttribute("data-beiseite");
      if (beiseite && knopf.name === "zettel") {
        var weg = document.createElement("button");
        weg.type = "button";
        weg.className = "quiet";
        weg.textContent = "Gehört nirgends hin";
        weg.addEventListener("click", function () {
          var f = document.createElement("form");
          f.method = "post";
          f.action = beiseite;
          [["token", knopf.form.elements.token.value], ["name", knopf.value], ["back", location.pathname]].forEach(function (paar) {
            var i = document.createElement("input");
            i.type = "hidden"; i.name = paar[0]; i.value = paar[1];
            f.appendChild(i);
          });
          document.body.appendChild(f);
          f.submit();
        });
        reihe.appendChild(weg);
      }
    }
    ja.addEventListener("click", function () { schliessen(); abschicken(knopf); });
    if (fragen) reihe.appendChild(ja);
    blase.appendChild(reihe);
    var ort = knopf.parentNode;
    ort.classList.add("sq-rueckfrage-ort");
    ort.appendChild(blase);
    if (knopf.classList.contains("sq-handkarte")) {
      // Über der gewählten Handkarte, aber nie über den Rand hinaus
      var links = Math.max(0, Math.min(knopf.offsetLeft - 20, ort.clientWidth - blase.offsetWidth));
      blase.style.left = links + "px";
      blase.style.setProperty("--pfeil", Math.max(12, knopf.offsetLeft - links + knopf.offsetWidth / 2 - 9) + "px");
    }
    // Oben kein Platz? Dann klappt die Blase nach unten auf
    var r = blase.getBoundingClientRect();
    if (r.top < 8) blase.classList.add("unten");
    (reihe.querySelector(".sq-siegelknopf") || ja).focus();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") schliessen(); });
})();

// Zurück aus dem Finder: Liegen neue Dateien im Lager, holt die Karte sie als Zettel in den Korb
(function () {
  var korb = document.querySelector("[data-lager-url]");
  if (!korb) return;
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    fetch(korb.dataset.lagerUrl, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && String(d.anzahl) !== korb.dataset.lager && !document.querySelector("dialog[open]")) {
          location.hash = "sq-korb";
          location.reload();
        }
      })
      .catch(function () {});
  });
})();

// Teil 1 des Fragebogens: eine Frage pro Bildschirm, wie ein Kartenstapel
(function () {
  var stapel = document.querySelector("[data-stapel]");
  if (!stapel) return;
  var karten = Array.prototype.slice.call(stapel.querySelectorAll(".sq-frage"));
  if (karten.length < 2) return;
  var form = stapel.closest("form");
  var fuss = form.querySelector(".sq-fb-fuss");
  var jetzt = 0;
  stapel.classList.add("stapel-an");
  var punkte = document.createElement("ol");
  punkte.className = "sq-stapel-punkte";
  punkte.setAttribute("aria-hidden", "true");
  karten.forEach(function () { punkte.appendChild(document.createElement("li")); });
  stapel.appendChild(punkte);

  karten.forEach(function (karte, i) {
    var reihe = document.createElement("p");
    reihe.className = "sq-stapel-knoepfe";
    if (i > 0) {
      var zurueck = document.createElement("button");
      zurueck.type = "button"; zurueck.className = "quiet"; zurueck.textContent = "← Zurück";
      zurueck.addEventListener("click", function () { zeige(i - 1); });
      reihe.appendChild(zurueck);
    }
    if (i < karten.length - 1) {
      var weiter = document.createElement("button");
      weiter.type = "button"; weiter.className = "sq-siegelknopf"; weiter.textContent = "Weiter →";
      weiter.addEventListener("click", function () { zeige(i + 1); });
      reihe.appendChild(weiter);
    }
    karte.appendChild(reihe);
    // Enter springt zur nächsten Frage statt gleich abzuschicken
    karte.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") && i < karten.length - 1) {
        e.preventDefault();
        zeige(i + 1);
      }
    });
  });

  function zeige(n) {
    jetzt = Math.max(0, Math.min(karten.length - 1, n));
    karten.forEach(function (k, i) {
      k.classList.toggle("dran", i === jetzt);
      k.classList.toggle("vorbei", i < jetzt);
    });
    Array.prototype.forEach.call(punkte.children, function (p, i) { p.classList.toggle("an", i <= jetzt); });
    if (fuss) fuss.classList.toggle("stapel-wartet", jetzt < karten.length - 1);
    var feld = karten[jetzt].querySelector("input:not([type=hidden]), select");
    if (feld && document.activeElement !== feld) setTimeout(function () { feld.focus({ preventScroll: true }); }, 250);
  }
  zeige(0);
})();

// Orden-Erklärung: am rechten Rand nach links aufklappen, damit nichts abgeschnitten wird
(function () {
  function ausrichten(e) {
    var li = e.target.closest && e.target.closest(".sq-orden li");
    if (!li) return;
    var r = li.getBoundingClientRect();
    li.classList.toggle("rechts", r.left + 220 > document.documentElement.clientWidth - 8);
  }
  document.addEventListener("mouseover", ausrichten);
  document.addEventListener("focusin", ausrichten);
})();

// "Ist drin" ohne gewählte Datei: gleich die Dateiauswahl öffnen statt einen Klick zu verschenken
(function () {
  document.addEventListener("click", function (e) {
    var knopf = e.target.closest("[data-datei-waehlen]");
    if (!knopf) return;
    var feld = knopf.form && knopf.form.querySelector("input[type=file]");
    if (!feld || (feld.files && feld.files.length)) return;
    e.preventDefault();
    feld.addEventListener("change", function () {
      if (!feld.files.length) return;
      var aktion = document.createElement("input");
      aktion.type = "hidden"; aktion.name = "aktion"; aktion.value = "datei";
      knopf.form.appendChild(aktion);
      knopf.form.submit();
    }, { once: true });
    feld.click();
  });
})();

// Eine angefangene Frage an die Beraterin geht beim Neuladen nicht verloren
(function () {
  var felder = document.querySelectorAll(".sq input[name=frage]");
  if (!felder.length) return;
  var speicher = null;
  try { speicher = window.localStorage; } catch (e) { return; }
  felder.forEach(function (feld) {
    var form = feld.form;
    var schluessel = "frits-frage:" + (form && form.action || location.pathname);
    try {
      var alt = speicher.getItem(schluessel);
      if (alt && !feld.value) {
        feld.value = alt;
        var auf = feld.closest("details");
        if (auf) auf.open = true;
      }
    } catch (e) {}
    feld.addEventListener("input", function () {
      try { feld.value ? speicher.setItem(schluessel, feld.value) : speicher.removeItem(schluessel); } catch (e) {}
    });
    if (form) form.addEventListener("submit", function () { try { speicher.removeItem(schluessel); } catch (e) {} });
  });
})();

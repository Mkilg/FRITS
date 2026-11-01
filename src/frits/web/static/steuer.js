/* Steuer-Quest: das Spielgefühl auf dem Kartentisch.
   - Zettel auf Provinzen oder Karten ziehen, Dateien vom Schreibtisch auf Karten ziehen
   - Stempel beim Ablegen (Papers, Please), tickernde Zähler mit steigender Tonhöhe (Balatro)
   - Konfetti und Funken bei Siegeln und beim Sieg
   Alles funktioniert auch ohne JavaScript; hier kommt nur die Würze dazu.
   Töne nur, wenn in FRITS "Klang an" ist. Bewegung nur, wenn der Computer sie nicht abgeschaltet hat. */
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
    // dumpfer Plopp: tiefer Ton, der schnell fällt, plus ein kurzes Rascheln
    var start = c.currentTime;
    var osc = c.createOscillator();
    var gain = c.createGain();
    osc.frequency.setValueAtTime(180, start);
    osc.frequency.exponentialRampToValueAtTime(55, start + 0.12);
    gain.gain.setValueAtTime(0.35, start);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
    osc.connect(gain).connect(c.destination);
    osc.start(start);
    osc.stop(start + 0.2);
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
    var zettel = e.target.closest && e.target.closest(".sq-zettel");
    if (!zettel) return;
    gezogen = zettel.getAttribute("data-name");
    e.dataTransfer.setData(TYP, gezogen);
    e.dataTransfer.setData("text/plain", gezogen);
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
      stempeln(input.form, e.clientX, e.clientY);
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
  if (document.querySelector(".flash.siegel, .flash.zeitalter, .flash.sieg")) {
    var f = document.querySelector(".flash.siegel, .flash.zeitalter, .flash.sieg");
    var p = mitte(f);
    funken(p[0], p[1], 30);
    fanfare();
  } else if (document.querySelector(".flash.beute")) {
    ton(784, 0.12, "triangle", 0.08);
    ton(1047, 0.2, "triangle", 0.08, 0.09);
  }
})();

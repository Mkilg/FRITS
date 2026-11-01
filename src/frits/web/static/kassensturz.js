/* Der Kassensturz, played back step by step.
   The page already shows the final numbers, so it works without JavaScript and with
   reduced motion. "Ruhig" counts up once, quietly. "Verspielt" plays every card and
   every spice, faster and faster, with little sounds that climb in pitch. */
(function () {
  "use strict";
  var root = document.querySelector("[data-kassensturz]");
  var raw = document.getElementById("ks-daten");
  if (!root || !raw) return;
  var data = JSON.parse(raw.textContent);
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // frits.js loads after this page's content, so ask it once everything is there.
  var playful = false;

  var el = function (name) { return root.querySelector('[data-ks="' + name + '"]'); };
  var koernerEl = el("koerner"), wuerzeEl = el("wuerze"), summeEl = el("summe"), stempel = el("stempel");
  var tisch = root.querySelector(".ks-tisch");
  var karten = Array.prototype.slice.call(root.querySelectorAll(".ks-karten [data-karte]"));
  var fritte = root.querySelector(".ks-fritte");
  var timers = [];

  function zahl(w) { return String(Math.round(w * 100) / 100).replace(".", ","); }
  function glas(id) { return root.querySelector('.ks-regal [data-gewuerz="' + id + '"]'); }
  function chip(i) { return karten[i] && karten[i].querySelector(".karte-koerner"); }
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function stopAll() { timers.forEach(clearTimeout); timers = []; }

  // ---------- sound: a short wooden "tock" that climbs with every step ----------
  var audio = null;
  function soundOn() { return window.FritsSound && window.FritsSound.enabled && window.FritsSound.enabled(); }
  function blip(step, kind) {
    if (!soundOn()) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try { audio = audio || new Ctx(); } catch (e) { return; }
    var t = audio.currentTime, osc = audio.createOscillator(), gain = audio.createGain();
    var base = kind === "mal" ? 220 : kind === "plus" ? 294 : 392;
    osc.type = kind === "mal" ? "triangle" : "sine";
    osc.frequency.setValueAtTime(base * Math.pow(2, Math.min(step, 24) / 12), t);
    gain.gain.setValueAtTime(kind === "mal" ? 0.22 : 0.12, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + (kind === "mal" ? 0.35 : 0.12));
    osc.connect(gain).connect(audio.destination);
    osc.start(t); osc.stop(t + 0.4);
  }

  function popup(target, text, kind) {
    if (!target) return;
    var p = document.createElement("span");
    p.className = "ks-plus " + (kind || "");
    p.textContent = text;
    target.appendChild(p);
    setTimeout(function () { p.remove(); }, 1100);
  }
  function bump(node, cls) {
    if (!node) return;
    node.classList.remove(cls); void node.offsetWidth; node.classList.add(cls);
  }
  function countUp(node, to, ms, done) {
    var start = null;
    function frame(now) {
      start = start || now;
      var t = Math.min(1, (now - start) / ms);
      node.textContent = Math.round(to * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(frame); else { node.textContent = to; if (done) done(); }
    }
    requestAnimationFrame(frame);
  }

  function reset() {
    stopAll();
    root.classList.add("laeuft");
    root.classList.remove("fertig");
    koernerEl.textContent = "0";
    wuerzeEl.textContent = zahl(data.grundwuerze || 1);
    summeEl.textContent = "?";
    stempel.classList.remove("drauf");
    ["ausbau", "erfolge"].forEach(function (n) { if (el(n)) el(n).classList.remove("da"); });
    karten.forEach(function (k, i) { k.classList.add("verdeckt"); k.classList.remove("aktiv"); chip(i).textContent = "+0"; });
  }
  function finish() {
    stopAll();
    root.classList.remove("laeuft");
    root.classList.add("fertig");
    koernerEl.textContent = data.koerner;
    wuerzeEl.textContent = zahl(data.wuerze);
    summeEl.textContent = data.summe;
    karten.forEach(function (k, i) { k.classList.remove("verdeckt", "aktiv"); chip(i).textContent = "+" + data.pro_karte[i]; });
    stempel.classList.add("drauf");
    ["ausbau", "erfolge"].forEach(function (n) { if (el(n)) el(n).classList.add("da"); });
    el("weiter").hidden = true;
    el("nochmal").hidden = reduced;
  }
  function ending(delay) {
    later(function () {
      karten.forEach(function (k) { k.classList.remove("aktiv"); });
      bump(summeEl.parentNode, "gross-werden");
      countUp(summeEl, data.summe, playful ? 900 : 600, function () {
        stempel.classList.add("drauf");
        if (window.Frits && window.Frits.stampSound && soundOn()) window.Frits.stampSound();
        if (playful && fritte) bump(fritte, "jubeln");
        if (playful && data.summe >= 50) bump(tisch, "funkeln");
        finish();
      });
    }, delay);
  }

  function calm() {
    reset();
    karten.forEach(function (k, i) { later(function () { k.classList.remove("verdeckt"); chip(i).textContent = "+" + data.pro_karte[i]; }, 60 * i); });
    var t = 60 * karten.length + 150;
    later(function () { countUp(koernerEl, data.koerner, 600); }, t);
    later(function () { wuerzeEl.textContent = zahl(data.wuerze); }, t + 650);
    ending(t + 800);
  }

  function play() {
    reset();
    var running = data.pro_karte.map(function () { return 0; });
    var t = 300;
    data.schritte.forEach(function (s, n) {
      var wait = Math.max(150, 480 - n * 25); // speeds up, like a good streak
      later(function () {
        karten.forEach(function (k) { k.classList.remove("aktiv"); });
        if (s.typ === "karte" || s.typ === "gewuerz") {
          var card = karten[s.karte];
          if (card) { card.classList.remove("verdeckt"); card.classList.add("aktiv"); }
          running[s.karte] += s.plus;
          if (chip(s.karte)) chip(s.karte).textContent = "+" + running[s.karte];
          popup(card, "+" + s.plus, s.typ === "gewuerz" ? "gewuerz" : "");
          if (s.typ === "gewuerz") bump(glas(s.gewuerz), "wackeln");
          koernerEl.textContent = s.koerner;
          bump(koernerEl.parentNode, "pochen");
          blip(n, "karte");
        } else if (s.typ === "plus") {
          bump(glas(s.gewuerz), "wackeln");
          popup(glas(s.gewuerz), "+" + zahl(s.plus) + " Würze", "wuerze");
          wuerzeEl.textContent = zahl(s.wuerze);
          bump(wuerzeEl.parentNode, "pochen");
          blip(n, "plus");
        } else if (s.typ === "mal") {
          bump(glas(s.gewuerz), "wackeln");
          popup(wuerzeEl.parentNode, "×" + zahl(s.mal), "mal");
          wuerzeEl.textContent = zahl(s.wuerze);
          bump(wuerzeEl.parentNode, "pochen");
          bump(tisch, "beben");
          blip(n, "mal");
        }
      }, t);
      t += wait;
    });
    // cards no spice touched still need to turn over
    later(function () { karten.forEach(function (k) { k.classList.remove("verdeckt"); }); }, t);
    ending(t + 250);
  }

  el("weiter").addEventListener("click", finish);
  el("nochmal").addEventListener("click", function () { el("nochmal").hidden = true; el("weiter").hidden = false; (playful ? play : calm)(); });
  function start() {
    playful = !!(window.Frits && window.Frits.playful && window.Frits.playful());
    if (reduced) { finish(); return; }
    el("weiter").hidden = false;
    (playful ? play : calm)();
  }
  if (document.readyState === "complete") start(); else window.addEventListener("load", start);
})();

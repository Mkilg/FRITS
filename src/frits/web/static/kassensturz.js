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
  // Into view when its lower edge hides below the window or under the sticky buttons and the
  // tab bar (that room is the node's scroll-margin-bottom).
  // The lowest y that nothing covers: above the tab bar and the sticky buttons, where they
  // are (Lena: at 1440 a card fully in sight still made the camera move and cut the jars).
  function frei() {
    var y = window.innerHeight;
    [root.querySelector(".ks-aktionen")].concat([].slice.call(document.querySelectorAll("nav"))).forEach(function (n) {
      if (!n) return;
      var pos = getComputedStyle(n).position, r = n.getBoundingClientRect();
      if ((pos === "fixed" || pos === "sticky") && r.height && r.top > window.innerHeight / 2 && r.bottom >= window.innerHeight - 80) y = Math.min(y, r.top);
    });
    return y;
  }
  function versteckt(node) {
    var r = node.getBoundingClientRect();
    return r.top < 0 || r.bottom > frei() - 6;
  }
  function insBild(node) {
    if (versteckt(node)) node.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  }
  // A slow drive of the page by dy pixels (Lena: after a long hand the jump to the new chest
  // tore the page away, 1000 px in half a second).
  function fahren(dy) {
    if (!dy) return;
    if (reduced) { window.scrollBy(0, dy); return; }
    var von = window.scrollY, ms = Math.min(1600, Math.max(500, Math.abs(dy) * 1.3)), t0 = null;
    function schritt(now) {
      t0 = t0 || now;
      var x = Math.min(1, (now - t0) / ms), e = x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
      window.scrollTo(0, von + dy * e);
      if (x < 1) requestAnimationFrame(schritt);
    }
    requestAnimationFrame(schritt);
  }
  // How far down until the node's lower edge sits above the buttons (its top stays in sight).
  function weg(node) {
    var r = node.getBoundingClientRect(), dy = r.bottom - (frei() - 16);
    return Math.max(0, Math.min(dy, r.top - 12));
  }
  // The Würze moment as a small note on top when its jar or the Würze box is out of sight
  // (Lena: with a long hand on the phone nobody saw the "+1 Würze").
  var zettelEl = null, zettelUhr = null;
  function zettel(quelle, text) {
    if (!((quelle && versteckt(quelle)) || versteckt(wuerzeEl.parentNode))) return;
    if (!zettelEl) { zettelEl = document.createElement("div"); zettelEl.className = "ks-zettel"; zettelEl.setAttribute("aria-hidden", "true"); root.appendChild(zettelEl); }
    var bild = quelle && quelle.querySelector(".glas-zeichen img, .karte-bild img");
    var name = quelle && (quelle.getAttribute("title") || "").split(":")[0];
    zettelEl.innerHTML = "";
    if (bild) { var i = document.createElement("img"); i.src = bild.src; i.alt = ""; zettelEl.appendChild(i); }
    var b = document.createElement("span");
    b.innerHTML = (name ? "<small></small>" : "") + "<strong></strong><em></em>";
    if (name) b.querySelector("small").textContent = name;
    b.querySelector("strong").textContent = text;
    b.querySelector("em").textContent = "Würze " + wuerzeEl.textContent;
    zettelEl.appendChild(b);
    bump(zettelEl, "zeigen");
    clearTimeout(zettelUhr);
    zettelUhr = setTimeout(function () { zettelEl.classList.remove("zeigen"); }, 1300);
  }
  function stopAll() { timers.forEach(clearTimeout); timers = []; }

  // ---------- sound: a short wooden "tock" that climbs with every step ----------
  var audio = null;
  function soundOn() { return window.FritsSound && window.FritsSound.enabled && window.FritsSound.enabled(); }
  function blip(step, kind) {
    if (!soundOn()) return;
    var base = kind === "mal" ? 220 : kind === "plus" ? 294 : 392;
    var f = base * Math.pow(2, Math.min(step, 24) / 12);
    // Inside the Klangrahmen the frame plays it: a sound made on this page itself would
    // stay silent until one clicks here once more.
    if (window.FritsSound.tone) {
      window.FritsSound.tone(f, kind === "mal" ? 0.35 : 0.12, kind === "mal" ? "triangle" : "sine", kind === "mal" ? 0.22 : 0.12);
      return;
    }
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try { audio = audio || new Ctx(); } catch (e) { return; }
    var t = audio.currentTime, osc = audio.createOscillator(), gain = audio.createGain();
    osc.type = kind === "mal" ? "triangle" : "sine";
    osc.frequency.setValueAtTime(f, t);
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
    ["ausbau", "saison", "erfolge", "truhen"].forEach(function (n) { if (el(n)) el(n).classList.remove("da"); });
    karten.forEach(function (k, i) { k.classList.add("verdeckt"); k.classList.remove("aktiv"); chip(i).textContent = "+0"; });
  }
  function finish(gestempelt) {
    stopAll();
    root.classList.remove("laeuft");
    root.classList.add("fertig");
    koernerEl.textContent = data.koerner;
    wuerzeEl.textContent = zahl(data.wuerze);
    summeEl.textContent = data.summe;
    karten.forEach(function (k, i) { k.classList.remove("verdeckt", "aktiv"); chip(i).textContent = "+" + data.pro_karte[i]; });
    stempel.classList.add("drauf");
    ["ausbau", "saison", "erfolge", "truhen"].forEach(function (n) { if (el(n)) el(n).classList.add("da"); });
    el("weiter").hidden = true;
    el("nochmal").hidden = reduced;
    // A finished stage, season goal, sticker or new chest below the fold: bring it into view once. After the
    // played round only once the stamp has landed and was seen (Lena: on the phone the page
    // jumped away right when the stamp came).
    // The lowest of them, so the ones above come along (Lena: the new chest stayed out of sight).
    var banner = (el("truhen") && el("truhen").classList.contains("mit-neuer") ? el("truhen") : null) || el("erfolge") || el("saison") || el("ausbau");
    if (banner && !gezeigt) {
      gezeigt = true;
      var zeigen = function () {
        fahren(weg(banner));
      };
      if (gestempelt === true) later(zeigen, 1600); else zeigen();
    }
  }
  var gezeigt = false;
  function ending(delay) {
    later(function () {
      karten.forEach(function (k) { k.classList.remove("aktiv"); });
      if (zettelEl) zettelEl.classList.remove("zeigen"); // not over the boxes on the way back (Lena)
      bump(summeEl.parentNode, "gross-werden");
      // The sum out of sight (below a tall shelf, or above after a long hand of cards): bring
      // Körner × Würze = Salzkörner into view before the stamp lands, so the stamp is seen
      // (Lena R43: it landed under the button bar, the page never moved).
      insBild(summeEl.closest(".ks-rechnung") || summeEl.parentNode);
      countUp(summeEl, data.summe, playful ? 900 : 600, function () {
        stempel.classList.add("drauf");
        if (window.Frits && window.Frits.stampSound && soundOn()) window.Frits.stampSound();
        if (playful && fritte) bump(fritte, "jubeln");
        if (playful && data.summe >= 50) bump(tisch, "funkeln");
        finish(true);
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
    // The cards are the show: on the phone they lay below the fold under a tall shelf (Lena:
    // nobody saw them flip). Bring the table up a moment after the start, the shelf stays
    // in sight above it as far as it fits.
    // On a wide screen only as far as the shelf stays whole (Lena: the jars were cut at 1440).
    var hoch = window.innerWidth > 760 ? Math.min(weg(tisch), root.querySelector(".ks-regal").getBoundingClientRect().top - 8) : weg(tisch);
    if (hoch > 0) { later(function () { window.scrollBy({ top: hoch, behavior: reduced ? "auto" : "smooth" }); }, 250); t += 450; }
    data.schritte.forEach(function (s, n) {
      var wait = Math.max(150, 480 - n * 25); // speeds up, like a good streak
      later(function () {
        karten.forEach(function (k) { k.classList.remove("aktiv"); });
        if (s.typ === "karte" || s.typ === "gewuerz") {
          var card = karten[s.karte];
          if (card) { card.classList.remove("verdeckt"); card.classList.add("aktiv"); insBild(card); } // the camera follows a long hand down
          running[s.karte] += s.plus;
          if (chip(s.karte)) chip(s.karte).textContent = "+" + running[s.karte];
          popup(card, "+" + s.plus, s.typ === "gewuerz" ? "gewuerz" : "");
          if (s.typ === "gewuerz") bump(glas(s.gewuerz), "wackeln");
          koernerEl.textContent = s.koerner;
          bump(koernerEl.parentNode, "pochen");
          blip(n, "karte");
        } else if (s.typ === "plus") {
          var quelle = glas(s.gewuerz) || karten[s.karte]; // a spice, or a card like Fritte's move-in
          bump(quelle, "wackeln");
          popup(quelle, "+" + zahl(s.plus) + " Würze", "wuerze");
          wuerzeEl.textContent = zahl(s.wuerze);
          zettel(quelle, "+" + zahl(s.plus) + " Würze");
          bump(wuerzeEl.parentNode, "pochen");
          blip(n, "plus");
        } else if (s.typ === "mal") {
          bump(glas(s.gewuerz), "wackeln");
          popup(wuerzeEl.parentNode, "×" + zahl(s.mal), "mal");
          wuerzeEl.textContent = zahl(s.wuerze);
          zettel(glas(s.gewuerz), "Würze ×" + zahl(s.mal));
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

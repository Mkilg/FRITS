/* FRITS: a few small helpers. Every page also works without JavaScript. */
(function () {
  "use strict";

  // Ask before steps that can't be undone (numbering an invoice, Storno, discarding).
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var button = e.submitter;
    var message = (button && button.getAttribute("data-confirm")) || form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) { e.preventDefault(); return; }
    var busy = form.getAttribute("data-busy");
    if (busy) {
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      var note = document.createElement("p");
      note.className = "busy";
      note.textContent = busy;
      form.appendChild(note);
    }
  });

  // "Im Text: 120 € übernehmen" puts that price into the field next to it.
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-fill]");
    if (!b) return;
    var input = b.parentElement.querySelector("input[name=price]");
    if (input) { input.value = b.getAttribute("data-fill"); input.focus(); }
  });

  // Drop zones: files dropped on the field (or picked) are sent right away.
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (input.matches && input.matches("[data-autosubmit]") && input.files && input.files.length) {
      input.form.requestSubmit ? input.form.requestSubmit() : input.form.submit();
    }
  });
  function zoneOf(e) { return e.target.closest && e.target.closest("[data-dropzone]"); }
  ["dragenter", "dragover"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      if (!document.querySelector("[data-dropzone]")) return;
      e.preventDefault();  // a file dropped next to the field must not open in the browser
      var zone = zoneOf(e);
      document.querySelectorAll("[data-dropzone].over").forEach(function (z) {
        if (z !== zone) z.classList.remove("over");
      });
      if (zone) zone.classList.add("over");
    });
  });
  document.addEventListener("dragleave", function (e) {
    var zone = zoneOf(e);
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove("over");
  });
  document.addEventListener("drop", function (e) {
    if (!document.querySelector("[data-dropzone]")) return;
    e.preventDefault();
    var zone = zoneOf(e) || document.querySelector("[data-dropzone]");
    zone.classList.remove("over");
    var input = zone.querySelector("input[type=file]");
    var dropped = e.dataTransfer && e.dataTransfer.files;
    if (!input || !dropped || !dropped.length) return;
    try { input.files = dropped; } catch (err) { return; }
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  // "Mehr" in the top row closes again when you click anywhere else.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.mehr[open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });

  // ---------- Stamps and the two kinds of joy (Einstellungen > Spiel-Effekte) ----------
  function playful() { return document.body.getAttribute("data-effekte") === "verspielt"; }
  function calmMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // A dull "plopp" made right here in the browser: a low tone that falls fast, plus a short rustle.
  // The Steuer-Quest uses the same sound.
  var audio = null;
  function stampSound() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try { audio = audio || new Ctx(); } catch (e) { return; }
    var c = audio, start = c.currentTime;
    var osc = c.createOscillator(), gain = c.createGain();
    osc.frequency.setValueAtTime(180, start);
    osc.frequency.exponentialRampToValueAtTime(55, start + 0.12);
    gain.gain.setValueAtTime(0.35, start);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
    osc.connect(gain).connect(c.destination);
    osc.start(start);
    osc.stop(start + 0.2);
    var length = Math.floor(c.sampleRate * 0.08);
    var buffer = c.createBuffer(1, length, c.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length);
    var noise = c.createBufferSource(), g2 = c.createGain();
    g2.gain.value = 0.12;
    noise.buffer = buffer;
    noise.connect(g2).connect(c.destination);
    noise.start(start);
  }

  // After an action: with a stamp, the plopp lands with it ("Verspielt" adds the chime too).
  function feedback(cue, stamped) {
    var sound = window.FritsSound;
    if (!sound || !sound.enabled()) return;
    if (!stamped) { sound.cue(cue); return; }
    setTimeout(stampSound, 300);
    if (playful()) setTimeout(function () { sound.cue(cue); }, 550);
  }

  // "Verspielt": sums count up from zero, e.g. "1.234,56 €" on the EÜR page.
  function countUp(el) {
    var text = el.textContent;
    var match = text.match(/-?[\d.]+(,\d+)?/);
    if (!match) return;
    var decimals = match[1] ? match[1].length - 1 : 0;
    var target = parseFloat(match[0].replace(/\./g, "").replace(",", "."));
    if (!isFinite(target) || target === 0) return;
    var format = function (v) {
      return v.toLocaleString("de-DE", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    };
    var before = text.slice(0, match.index), after = text.slice(match.index + match[0].length);
    var started = null, ms = 800;
    function step(now) {
      started = started || now;
      var t = Math.min(1, (now - started) / ms);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = before + format(target * eased) + after;
      if (t < 1) requestAnimationFrame(step); else el.textContent = text;
    }
    requestAnimationFrame(step);
  }
  function start() {
    if (playful() && !calmMotion()) document.querySelectorAll("[data-zaehlen]").forEach(countUp);
  }
  // Sound switched off: forget the music that should carry on to the next page (frits-sound.js).
  if (document.body.getAttribute("data-frits-sound") !== "on") {
    try { sessionStorage.removeItem("frits-music"); } catch (e) { /* egal */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  // ---------- Feierabend: the sound plays right on the click ----------
  // Browsers (Safari most of all) only play sound when it starts from a click, and a page
  // change cuts it off. So with sound on, FRITS plays the Feierabend sound on the click,
  // fetches the Feierabend page and swaps it in, while the sound keeps ringing.
  // If anything goes wrong, the form is simply sent the normal way.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var sound = window.FritsSound;
    if (!form.hasAttribute("data-feierabend") || !sound || !sound.enabled() || !window.fetch) return;
    if (e.defaultPrevented) return;
    e.preventDefault();
    sound.music.stop();
    sound.cue("shutdown");
    document.body.classList.add("frits-ausblenden");
    var waited = new Promise(function (done) { setTimeout(done, 350); });
    var loaded = fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text().then(function (html) { return { url: r.url, html: html }; });
      });
    Promise.all([loaded, waited]).then(function (both) {
      var next = new DOMParser().parseFromString(both[0].html, "text/html");
      document.title = next.title;
      next.body.classList.add("frits-einblenden");
      document.body.replaceWith(document.adoptNode(next.body));
      if (both[0].url && both[0].url !== location.href) {
        history.pushState({ feierabend: true }, "", both[0].url);
        swapped = true;
      }
      window.scrollTo(0, 0);
      start();
    }).catch(function () {
      form.removeAttribute("data-feierabend");
      form.submit();
    });
  });
  // The page was swapped in by hand, so "Zurück" in the browser loads the earlier page fresh.
  var swapped = false;
  window.addEventListener("popstate", function () { if (swapped) location.reload(); });

  window.Frits = { feedback: feedback, stampSound: stampSound, playful: playful };
})();

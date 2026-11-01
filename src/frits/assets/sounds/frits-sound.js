/*
 * FRITS sound player (browser side). No libraries, works offline.
 *
 * Sound is OFF unless the page says otherwise. The web app turns it on with
 *   <body data-frits-sound="on" data-frits-sound-base="/static/sounds/">
 *
 * Play a cue from JavaScript:      FritsSound.cue("done")
 * The stamp "plopp":               FritsSound.stamp()
 * A finished invoice:              FritsSound.ching()
 * Play a cue from the server:      HX-Trigger: {"frits-cue": "sent"}   (htmx response header)
 * Focus music:                     FritsSound.music.play("morgen") / .stop() / .toggle()
 * Buttons without any JavaScript:  <button data-frits-cue="done">, <button data-frits-music="morgen">
 *
 * Cues: done, sent, shutdown, focus.  Tracks: morgen, buero-block, schwung, abenteuer.
 *
 * The "Klangrahmen": browsers (Safari most of all) only play sound that a click started on
 * the very page, and every click in FRITS loads a new page. That cut the music off and kept
 * the stamp silent. So with sound on, the first page change keeps this page as a quiet frame
 * around all following pages: the sound lives here and never stops, and the pages inside
 * ask it to play. The address bar and the tab title follow the page inside.
 */
(function () {
  "use strict";

  var CUE_VOLUME = 0.6;
  var MUSIC_VOLUME = 0.5;
  var FADE_MS = 2500;
  var CUES = ["done", "sent", "shutdown", "focus"];
  var FRAME = "frits-rahmen";

  function body() { return document.body || document.documentElement; }
  function base() {
    var b = body().getAttribute("data-frits-sound-base") || "/static/sounds/";
    return b.charAt(b.length - 1) === "/" ? b : b + "/";
  }
  function enabled() { return body().getAttribute("data-frits-sound") === "on"; }

  // Music buttons show whether their track is on (aria-pressed). A button with
  // data-label-playing="♪ Musik läuft" also swaps to that text while it plays.
  function showPlaying(doc, playing) {
    doc.querySelectorAll("[data-frits-music]").forEach(function (el) {
      var on = el.getAttribute("data-frits-music") === playing;
      el.setAttribute("aria-pressed", on ? "true" : "false");
      var label = el.getAttribute("data-label-playing");
      if (!label) return;
      if (!el.hasAttribute("data-label")) el.setAttribute("data-label", el.textContent);
      el.textContent = on ? label : el.getAttribute("data-label");
    });
  }
  function announce(doc, playing) {
    var b = doc && doc.body;
    if (!b) return;
    var Ev = (doc.defaultView && doc.defaultView.CustomEvent) || CustomEvent;
    b.dispatchEvent(new Ev("frits-music", { bubbles: true, detail: { playing: playing } }));
  }

  // The parts every page has, whether it plays itself or asks the frame around it.
  function wire(sound) {
    document.addEventListener("click", function (e) {
      var el = e.target.closest && e.target.closest("[data-frits-cue],[data-frits-music]");
      if (!el) return;
      if (el.hasAttribute("data-frits-cue")) sound.cue(el.getAttribute("data-frits-cue"));
      if (el.hasAttribute("data-frits-music")) sound.music.toggle(el.getAttribute("data-frits-music"));
    });
    document.addEventListener("frits-music", function (e) {
      showPlaying(document, e.detail && e.detail.playing);
    });
    // htmx turns the HX-Trigger response header into an event on <body>.
    document.addEventListener("frits-cue", function (e) {
      var d = e.detail;
      sound.cue(typeof d === "string" ? d : d && (d.value || d.name));
    });
    window.FritsSound = sound;
  }

  function whenReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  // ---------- A page inside the Klangrahmen: the frame plays ----------
  var host = null;
  try {
    if (window.parent !== window && window.parent.FritsSound && window.parent.FritsSound.rahmen) {
      host = window.parent.FritsSound;
    }
  } catch (e) { host = null; }  // another site around FRITS: play here, as usual

  if (host) {
    // Clicks here count as clicks for the frame too: it unlocks its sound with them.
    var unlock = function () { host.unlock(); };
    document.addEventListener("pointerdown", unlock, true);
    document.addEventListener("keydown", unlock, true);
    var inner = {
      cue: function (name) { if (enabled()) host.cue(name); },
      stamp: function () { if (enabled()) host.stamp(); },
      ching: function () { if (enabled()) host.ching(); },
      music: {
        play: function (name, from, ms) { if (enabled()) host.music.play(name, from, ms); },
        stop: function (switching) { host.music.stop(switching); },
        toggle: function (name) { if (enabled()) host.music.toggle(name); },
        playing: function () { return host.music.playing(); }
      },
      setEnabled: function (on) {
        body().setAttribute("data-frits-sound", on ? "on" : "off");
        host.setEnabled(on);
      },
      enabled: enabled
    };
    wire(inner);
    // A page that changes the address itself (the Feierabend swap) tells the frame, so the
    // address bar and the tab title follow.
    ["pushState", "replaceState"].forEach(function (name) {
      var original = history[name];
      history[name] = function () {
        var result = original.apply(history, arguments);
        try { host.follow(); } catch (e) { /* egal */ }
        return result;
      };
    });
    whenReady(function () {
      var playing = host.music.playing();
      showPlaying(document, playing);
      // The page's own music button decides: the Steuer-Quest switches to its adventure
      // track, and leaving it switches back to the chosen mood.
      var main = document.querySelector("[data-frits-music-main]");
      var track = main && main.getAttribute("data-frits-music");
      if (playing && track && track !== playing && enabled()) host.music.play(track, 0, 800);
    });
    return;
  }

  // ---------- This page plays itself (and may become the Klangrahmen) ----------

  // A new fade on the same audio replaces the old one, so fade-in and fade-out never fight.
  // k is kept between 0 and 1: the first frame's time can lie just before `start`, and a
  // volume below 0 makes the browser throw, which used to leave the music playing silently.
  function fade(audio, to, ms, done) {
    var from = audio.volume, start = performance.now(), id = {};
    audio.fritsFade = id;
    function step(now) {
      if (audio.fritsFade !== id) return;
      var k = Math.max(0, Math.min(1, (now - start) / ms));
      audio.volume = Math.max(0, Math.min(1, from + (to - from) * k));
      if (k < 1) requestAnimationFrame(step); else if (done) done();
    }
    requestAnimationFrame(step);
  }

  // One audio element per cue and one for the music, kept for the whole visit: once a click
  // has unlocked an element, it may play again later without a click.
  var cues = {};
  function cueAudio(name) {
    return cues[name] || (cues[name] = new Audio(base() + "cues/" + name + ".mp3"));
  }
  function cue(name) {
    if (!enabled() || !name) return;
    var a = cueAudio(name);
    a.muted = false;
    a.volume = CUE_VOLUME;
    try { a.currentTime = 0; } catch (e) { /* not loaded yet */ }
    // Before the first click browsers block sound; that's fine, stay quiet.
    var p = a.play();
    if (p && p.catch) p.catch(function () {});
  }

  // A dull "plopp" made right here: a low tone that falls fast, plus a short rustle.
  var ctx = null;
  function context() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!ctx && Ctx) { try { ctx = new Ctx(); } catch (e) { ctx = null; } }
    return ctx;
  }
  function stamp() {
    if (!enabled()) return;
    var c = context();
    if (!c) return;
    if (c.state === "suspended" && c.resume) c.resume();
    var start = c.currentTime;
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

  // "Ka-tsching" for a finished invoice: a short rattle, then two bright bell tones that
  // ring out, like an old shop till. Also made right here, no file needed.
  function ching() {
    if (!enabled()) return;
    var c = context();
    if (!c) return;
    if (c.state === "suspended" && c.resume) c.resume();
    var t = c.currentTime;
    var length = Math.floor(c.sampleRate * 0.05);
    var buffer = c.createBuffer(1, length, c.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length);
    var rattle = c.createBufferSource(), rg = c.createGain(), hp = c.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 2500;
    rg.gain.value = 0.18;
    rattle.buffer = buffer;
    rattle.connect(hp).connect(rg).connect(c.destination);
    rattle.start(t);
    [[1318.5, 0.08, 0.22], [1760, 0.16, 0.2], [2637, 0.16, 0.06]].forEach(function (bell) {
      var osc = c.createOscillator(), g = c.createGain(), at = t + bell[1];
      osc.type = "sine";
      osc.frequency.setValueAtTime(bell[0], at);
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(bell[2], at + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, at + 1.1);
      osc.connect(g).connect(c.destination);
      osc.start(at);
      osc.stop(at + 1.2);
    });
  }

  // Every click in FRITS without the Klangrahmen loads a new page, which would cut the music
  // off. So FRITS remembers in this browser tab which track plays and where, and carries on
  // after the page change (or after reloading the frame). Pressing ♪ Musik again, switching
  // sound off or Feierabend ends that wish. sessionStorage is forgotten when the tab closes.
  var WISH = "frits-music";
  function saveWish(track, time) {
    try {
      if (track) sessionStorage.setItem(WISH, JSON.stringify({ track: track, time: time || 0 }));
      else sessionStorage.removeItem(WISH);
    } catch (e) { /* private window: music just doesn't carry over */ }
  }
  function loadWish() {
    try { return JSON.parse(sessionStorage.getItem(WISH) || "null"); } catch (e) { return null; }
  }

  var rahmen = null;  // the frame, once this page became the Klangrahmen
  function tell(playing) {
    announce(document, playing);
    if (rahmen) { try { announce(rahmen.contentDocument, playing); } catch (e) { /* a PDF */ } }
  }

  var waiting = false;  // the music wants to play but the browser waits for a click
  var music = {
    audio: null,
    track: null,
    play: function (name, from, fadeMs) {
      if (!enabled() || !name) return;
      if (this.track === name) return;
      var old = this.track;
      var a = this.audio || (this.audio = new Audio());
      this.track = name;
      saveWish(name, from);
      var self = this;
      function start() {
        if (self.track !== name) return;
        a.src = base() + "music/" + name + ".mp3";
        a.loop = true;
        a.volume = 0;
        if (from) { try { a.currentTime = from; } catch (e) { /* starts at 0 */ } }
        waiting = false;
        var p = a.play();
        if (p && p.catch) p.catch(function () { waiting = self.track === name; });
        fade(a, MUSIC_VOLUME, fadeMs || FADE_MS);
      }
      // Switching tracks: the old one fades out first. Same element, so no new click is needed.
      if (old && !a.paused) fade(a, 0, 500, start); else start();
      tell(name);
    },
    stop: function (switching) {
      if (!switching) saveWish(null);
      if (!this.track) return;
      var a = this.audio;
      this.track = null;
      waiting = false;
      fade(a, 0, FADE_MS / 2, function () { a.pause(); });
      tell(null);
    },
    toggle: function (name) {
      if (this.track === name) this.stop(); else this.play(name);
    },
    playing: function () { return this.track; }
  };

  // The first click or key press unlocks sound for the whole visit: the cues, the plopp, and
  // music that was waiting (Safari only allows sound that a click started).
  var unlocked = false;
  function unlock() {
    if (!enabled()) return;
    var c = context();
    if (c && c.state === "suspended" && c.resume) c.resume();
    if (waiting && music.audio && music.track) {
      waiting = false;
      var p = music.audio.play();
      if (p && p.catch) p.catch(function () { waiting = true; });
    }
    if (unlocked) return;
    unlocked = true;
    CUES.forEach(function (name) {
      var a = cueAudio(name);
      if (!a.paused) return;
      a.muted = true;
      var p = a.play();
      var done = function () { if (a.muted) { a.pause(); a.muted = false; } };
      if (p && p.then) p.then(done, function () { a.muted = false; unlocked = false; });
      else done();
    });
  }
  document.addEventListener("pointerdown", unlock, true);
  document.addEventListener("keydown", unlock, true);

  window.addEventListener("pagehide", function () {
    if (music.track && music.audio) saveWish(music.track, music.audio.currentTime);
  });

  function resume() {
    var wish = loadWish();
    if (!wish || !wish.track || !enabled()) return;
    if (document.querySelector(".feierabend-page")) { saveWish(null); return; }
    var main = document.querySelector("[data-frits-music-main]");
    var track = main ? main.getAttribute("data-frits-music") : wish.track;
    music.play(track, track === wish.track ? wish.time : 0, 800);
  }

  function setEnabled(on) {
    body().setAttribute("data-frits-sound", on ? "on" : "off");
    if (!on) music.stop();
  }

  // ---------- The Klangrahmen ----------
  var topLevel = window.parent === window;
  function frame() {
    if (rahmen) return rahmen;
    rahmen = document.createElement("iframe");
    rahmen.name = FRAME;
    rahmen.title = "FRITS";
    rahmen.setAttribute("data-frits-rahmen", "");
    rahmen.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;border:0;margin:0;padding:0;" +
      "visibility:hidden;z-index:2147483647;background:transparent";
    rahmen.addEventListener("load", arrived);
    body().appendChild(rahmen);
    return rahmen;
  }
  function arrived() {
    var w, d, href;
    try { w = rahmen.contentWindow; d = rahmen.contentDocument; href = w.location.href; } catch (e) { return; }
    if (!d || href === "about:blank") return;
    if (!rahmen.hasAttribute("data-bereit")) {
      // From now on this page is only the quiet frame: its own content goes away.
      rahmen.setAttribute("data-bereit", "");
      Array.prototype.slice.call(body().childNodes).forEach(function (n) {
        if (n !== rahmen) body().removeChild(n);
      });
      document.documentElement.style.height = "100%";
      document.documentElement.style.overflow = "hidden";
      body().style.cssText = "margin:0;height:100%;overflow:hidden;background:none";
      rahmen.style.visibility = "visible";
    }
    try { w.focus(); } catch (e) { /* egal */ }
    follow();
    // A FRITS page inside says whether sound is on (Einstellungen can switch it off).
    if (d.body && d.body.hasAttribute("data-effekte")) {
      var on = d.body.getAttribute("data-frits-sound") === "on";
      if (on !== enabled()) setEnabled(on);
      var b = d.body.getAttribute("data-frits-sound-base");
      if (b) body().setAttribute("data-frits-sound-base", b);
    }
  }
  // The address bar and the tab title show the page inside the frame.
  function follow() {
    if (!rahmen) return;
    try {
      var w = rahmen.contentWindow, d = rahmen.contentDocument;
      history.replaceState(history.state, "", w.location.pathname + w.location.search + w.location.hash);
      if (d.title) document.title = d.title;
    } catch (e) { /* a PDF or another site: keep what is there */ }
  }
  function useFrame() { return topLevel && enabled() && !!window.URL; }
  function samePlace(url) {
    try { return new URL(url, location.href).origin === location.origin; } catch (e) { return false; }
  }

  // Window listeners run after every listener on the page, so a click or form that the page
  // handles itself (a confirm, the Feierabend swap) has already said so with preventDefault.
  window.addEventListener("click", function (e) {
    if (!useFrame() || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.hasAttribute("download") || a.hasAttribute("data-ohne-rahmen")) return;
    if (a.target && a.target !== "_self") return;
    if (!samePlace(a.href)) return;
    var url = new URL(a.href, location.href);
    if (url.hash && url.pathname === location.pathname && url.search === location.search) return;
    e.preventDefault();
    frame().src = url.href;
  });
  window.addEventListener("submit", function (e) {
    if (!useFrame() || e.defaultPrevented) return;
    var form = e.target;
    var aim = (e.submitter && e.submitter.getAttribute("formtarget")) || form.getAttribute("target");
    if ((aim && aim !== "_self") || form.method === "dialog") return;
    if (!samePlace(form.action) || form.hasAttribute("data-ohne-rahmen")) return;
    frame();
    form.target = FRAME;
  });
  // form.submit() from a script sends no submit event; it goes into the frame too.
  if (topLevel && window.HTMLFormElement) {
    var send = HTMLFormElement.prototype.submit;
    HTMLFormElement.prototype.submit = function () {
      var aim = this.getAttribute("target");
      if (useFrame() && (!aim || aim === "_self") && samePlace(this.action)) {
        frame();
        this.target = FRAME;
      }
      return send.call(this);
    };
  }

  var sound = {
    cue: cue,
    stamp: stamp,
    ching: ching,
    music: music,
    setEnabled: setEnabled,
    enabled: enabled,
    unlock: unlock,
    follow: follow,
    rahmen: topLevel
  };
  wire(sound);
  whenReady(resume);
})();

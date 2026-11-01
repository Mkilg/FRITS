/*
 * FRITS sound player (browser side). No libraries, works offline.
 *
 * Sound is OFF unless the page says otherwise. The web app turns it on with
 *   <body data-frits-sound="on" data-frits-sound-base="/static/sounds/">
 *
 * Play a cue from JavaScript:      FritsSound.cue("done")
 * Play a cue from the server:      HX-Trigger: {"frits-cue": "sent"}   (htmx response header)
 * Focus music:                     FritsSound.music.play("morgen") / .stop() / .toggle()
 * Buttons without any JavaScript:  <button data-frits-cue="done">, <button data-frits-music="morgen">
 *
 * Cues: done, sent, shutdown, focus.  Tracks: morgen, buero-block, schwung, abenteuer.
 * Music fades in and out slowly and never starts by itself. Once started, it carries on
 * across page changes in the same tab until it is switched off.
 *
 * How the music survives page changes ("Rahmen"):
 * Every click in FRITS loads a new page. Safari (and the Mac app, which opens Safari) only
 * plays sound after a click on the page itself, so music that restarts on each new page
 * stays silent. When music starts, FRITS therefore keeps this page as a quiet frame that
 * holds the player and shows the app inside a full-size <iframe>. Clicks and forms change
 * only the inner page; the frame, and the music in it, stays. The inner pages hand music
 * and cues to the frame. If the tab is reloaded, FRITS remembers the track and position
 * (sessionStorage) and continues, at the latest with the next click.
 */
(function () {
  "use strict";

  var CUE_VOLUME = 0.6;
  var MUSIC_VOLUME = 0.5;
  var FADE_MS = 2500;
  var CUES = ["done", "sent", "shutdown", "focus"];

  function body() { return document.body || document.documentElement; }
  function base() {
    var b = body().getAttribute("data-frits-sound-base") || "/static/sounds/";
    return b.charAt(b.length - 1) === "/" ? b : b + "/";
  }
  function enabled() { return body().getAttribute("data-frits-sound") === "on"; }

  // ---------- Inside the frame: hand everything to the player outside ----------
  var host = null;
  try {
    if (window.top !== window && window.top.FritsSound && window.top.FritsSound.isFrame()) {
      host = window.top.FritsSound;
    }
  } catch (e) { host = null; /* another site framed us: play on our own */ }

  // Music buttons show whether their track is on (aria-pressed). A button with
  // data-label-playing="♪ Musik läuft" also swaps to that text while it plays, and to
  // data-label-waiting while the browser still waits for a click before it may play.
  function showState(doc, playing, waiting) {
    doc.querySelectorAll("[data-frits-music]").forEach(function (el) {
      var on = el.getAttribute("data-frits-music") === playing;
      el.setAttribute("aria-pressed", on ? "true" : "false");
      var label = el.getAttribute("data-label-playing");
      if (!label) return;
      if (!el.hasAttribute("data-label")) el.setAttribute("data-label", el.textContent);
      var wait = el.getAttribute("data-label-waiting") || el.getAttribute("data-label");
      el.textContent = on ? (waiting ? wait : label) : el.getAttribute("data-label");
    });
  }

  if (host) {
    var proxy = {
      cue: function (name) { if (enabled()) host.cue(name); },
      music: host.music,
      setEnabled: function (on) { if (!on) host.music.stop(); },
      enabled: enabled,
      isFrame: function () { return false; }
    };
    document.addEventListener("click", function (e) {
      var el = e.target.closest && e.target.closest("[data-frits-cue],[data-frits-music]");
      if (!el) return;
      if (el.hasAttribute("data-frits-cue")) proxy.cue(el.getAttribute("data-frits-cue"));
      if (el.hasAttribute("data-frits-music")) host.music.toggle(el.getAttribute("data-frits-music"));
    });
    document.addEventListener("frits-cue", function (e) {
      var d = e.detail;
      proxy.cue(typeof d === "string" ? d : d && (d.value || d.name));
    });
    var arrive = function () { host.arrived(document); };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", arrive);
    else arrive();
    window.FritsSound = proxy;
    return;
  }

  // ---------- The player (in the frame, or on a plain page before the frame exists) ----------

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

  var cache = {};
  function cueAudio(name) {
    return cache[name] || (cache[name] = new Audio(base() + "cues/" + name + ".mp3"));
  }
  function cue(name) {
    if (!enabled() || !name) return;
    var a = cueAudio(name);
    a.muted = false;
    a.volume = CUE_VOLUME;
    a.currentTime = 0;
    // Browsers block sound until the person has clicked something; that's fine, stay quiet.
    var p = a.play();
    if (p && p.catch) p.catch(function () {});
  }
  // Safari lets an audio element play later on its own once it has played after a click.
  // So on a click, every cue is played once without sound: later cues (stamps, "Erledigt!")
  // can then ring after a page change too.
  var unlocked = false;
  function unlockCues() {
    if (unlocked) return;
    unlocked = true;
    unlockTracks();
    CUES.forEach(function (name) {
      var a = cueAudio(name);
      a.muted = true;
      var p = a.play();
      var reset = function () { a.pause(); a.currentTime = 0; a.muted = false; };
      if (p && p.then) p.then(reset, function () { a.muted = false; unlocked = false; });
      else reset();
    });
  }

  // One audio element per track, kept for the whole visit. Safari lets an element that
  // once played after a click play again later, so switching tracks (Steuer-Quest) and
  // coming back works without another click, and each track continues where it was.
  var TRACKS = ["morgen", "buero-block", "schwung", "abenteuer"];
  var tracks = {};
  function trackAudio(name) {
    var a = tracks[name];
    if (!a) {
      a = tracks[name] = new Audio(base() + "music/" + name + ".mp3");
      a.loop = true;
      a.volume = 0;
    }
    return a;
  }
  function unlockTracks() {
    TRACKS.forEach(function (name) {
      var a = trackAudio(name);
      if (a === music.audio || !a.paused) return;
      a.muted = true;
      var p = a.play();
      var done = function () { if (music.audio !== a) a.pause(); a.muted = false; };
      if (p && p.then) p.then(done, function () { a.muted = false; });
      else done();
    });
  }

  // Track and position, remembered for this tab in case it is reloaded.
  var WISH = "frits-music";
  function store(key, value) {
    try {
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    } catch (e) { /* private window: music just doesn't carry over */ }
  }
  function load(key) {
    try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch (e) { return null; }
  }

  // Pages that show the player's state: this one, and the page inside the frame.
  var pages = [document];
  function announce() {
    var playing = music.track, waiting = music.waiting;
    pages = pages.filter(function (doc) { try { return !!doc.defaultView; } catch (e) { return false; } });
    pages.forEach(function (doc) {
      try {
        showState(doc, playing, waiting);
        var Ev = doc.defaultView.CustomEvent;
        (doc.body || doc.documentElement).dispatchEvent(
          new Ev("frits-music", { bubbles: true, detail: { playing: playing, waiting: waiting } }));
      } catch (e) { /* that page is already gone */ }
    });
  }

  var music = {
    audio: null,
    track: null,
    waiting: false,
    play: function (name, from, fadeMs) {
      if (!enabled() || !name) return;
      if (this.audio && this.track === name) { if (this.waiting) this.retry(); return; }
      this.stop(true);
      var a = trackAudio(name);
      a.muted = false;
      if (from !== undefined) { try { a.currentTime = from; } catch (e) { /* not loaded yet */ } }
      this.audio = a;
      this.track = name;
      this.waiting = false;
      store(WISH, { track: name, time: a.currentTime || from || 0 });
      var self = this;
      if (!a.fritsWatched) {
        a.fritsWatched = true;
        a.addEventListener("playing", function () {
          if (self.audio === a && self.waiting) { self.waiting = false; announce(); }
        });
      }
      var p = a.play();
      if (p && p.catch) p.catch(function () {
        if (self.audio !== a) return;
        self.waiting = true;
        announce();
        waitForClick();
      });
      fade(a, MUSIC_VOLUME, fadeMs || FADE_MS);
      announce();
      enterFrame();
    },
    retry: function () {
      var a = this.audio;
      if (!a) return;
      var p = a.play();
      if (p && p.catch) p.catch(function () {});
      enterFrame();
    },
    stop: function (switching) {
      var a = this.audio;
      if (!switching) store(WISH, null);
      if (!a) return;
      this.audio = null;
      this.track = null;
      this.waiting = false;
      fade(a, 0, FADE_MS / 2, function () { a.pause(); });
      announce();
    },
    toggle: function (name) {
      if (this.audio && this.track === name) {
        if (this.waiting) this.retry(); else this.stop();
      } else this.play(name);
    },
    playing: function () { return this.waiting ? null : this.track; }
  };

  // Safari only allows sound after a click on the page. Then the music waits, the button
  // says so ("♪ Weiterhören"), and it starts with the next click or key press anywhere.
  var listening = false;
  function waitForClick() {
    if (listening) return;
    listening = true;
    pages.forEach(function (doc) {
      doc.addEventListener("pointerdown", onGesture, true);
      doc.addEventListener("keydown", onGesture, true);
    });
  }
  function onGesture() {
    if (!music.audio || !music.waiting) return;
    music.retry();
    unlockCues();
  }

  // ---------- The frame ----------
  var frame = null;
  function enterFrame() {
    if (frame || window.top !== window || !window.history || !document.body) return;
    // A page that answered a form (POST) can't simply be loaded again. The music plays
    // here then, and the next page builds the frame.
    if (!body().hasAttribute("data-frits-get")) return;
    unlockCues();
    var start = location.href;
    var x = window.scrollX, y = window.scrollY;
    frame = document.createElement("iframe");
    frame.id = "frits-rahmen";
    frame.title = document.title;
    frame.setAttribute("style", "position:fixed;inset:0;width:100%;height:100%;border:0;margin:0;" +
      "background:transparent;display:block");
    frame.src = start;
    var first = true;
    frame.addEventListener("load", function () {
      var win, doc;
      try { win = frame.contentWindow; doc = frame.contentDocument; } catch (e) { return; }
      if (!doc || !win || win.location.origin !== location.origin) return;
      document.title = doc.title;
      try { history.replaceState(null, "", win.location.href); } catch (e) { /* egal */ }
      if (first) { first = false; win.scrollTo(x, y); }
      win.focus();
    });
    var keep = body();
    while (keep.firstChild) keep.removeChild(keep.firstChild);
    keep.setAttribute("style", "margin:0;overflow:hidden");
    keep.className = "";
    keep.appendChild(frame);
  }

  // A page inside the frame has loaded.
  function arrived(doc) {
    if (pages.indexOf(doc) < 0) pages.push(doc);
    if (listening) {
      doc.addEventListener("pointerdown", onGesture, true);
      doc.addEventListener("keydown", onGesture, true);
    }
    var view = doc.defaultView;
    // Feierabend and "FRITS ist aus" end the music.
    if (doc.querySelector(".feierabend-page") || doc.getElementById("frits-waiting")) {
      music.stop();
      return;
    }
    // The page's own music button decides: the Steuer-Quest switches to its adventure
    // track, and leaving it switches back to the chosen mood where it was.
    var main = doc.querySelector("[data-frits-music-main]");
    if (music.audio && main && main.getAttribute("data-frits-music") !== music.track) {
      music.play(main.getAttribute("data-frits-music"), undefined, 1200);
    }
    showState(doc, music.track, music.waiting);
    if (view) view.addEventListener("pagehide", function () {
      pages = pages.filter(function (d) { return d !== doc; });
    });
  }

  // Reloaded tab, or a plain page: carry on with the remembered music.
  window.addEventListener("pagehide", function () {
    if (music.audio) store(WISH, { track: music.track, time: music.audio.currentTime });
  });
  function resume() {
    var wish = load(WISH);
    if (!wish || !wish.track || !enabled()) return;
    if (document.querySelector(".feierabend-page") || document.getElementById("frits-waiting")) {
      store(WISH, null);
      return;
    }
    var main = document.querySelector("[data-frits-music-main]");
    var track = main ? main.getAttribute("data-frits-music") : wish.track;
    music.play(track, track === wish.track ? wish.time : undefined, 800);
  }

  function setEnabled(on) {
    body().setAttribute("data-frits-sound", on ? "on" : "off");
    if (!on) music.stop();
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-frits-cue],[data-frits-music]");
    if (!el) return;
    unlockCues();
    if (el.hasAttribute("data-frits-cue")) cue(el.getAttribute("data-frits-cue"));
    if (el.hasAttribute("data-frits-music")) music.toggle(el.getAttribute("data-frits-music"));
  });

  // htmx turns the HX-Trigger response header into an event on <body>.
  document.addEventListener("frits-cue", function (e) {
    var d = e.detail;
    cue(typeof d === "string" ? d : d && (d.value || d.name));
  });

  window.FritsSound = {
    cue: cue,
    music: music,
    setEnabled: setEnabled,
    enabled: enabled,
    isFrame: function () { return !!frame; },
    arrived: arrived
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", resume);
  else resume();
})();

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
 * Cues: done, sent, shutdown, focus.  Tracks: morgen, buero-block.
 * Music fades in and out slowly and never starts by itself. Once started, it carries on
 * across page changes in the same tab until it is switched off.
 */
(function () {
  "use strict";

  var CUE_VOLUME = 0.6;
  var MUSIC_VOLUME = 0.5;
  var FADE_MS = 2500;

  function body() { return document.body || document.documentElement; }
  function base() {
    var b = body().getAttribute("data-frits-sound-base") || "/static/sounds/";
    return b.charAt(b.length - 1) === "/" ? b : b + "/";
  }
  function enabled() { return body().getAttribute("data-frits-sound") === "on"; }

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
  function cue(name) {
    if (!enabled()) return;
    var a = cache[name] || (cache[name] = new Audio(base() + "cues/" + name + ".mp3"));
    a.volume = CUE_VOLUME;
    a.currentTime = 0;
    // Browsers block sound until the person has clicked something; that's fine, stay quiet.
    var p = a.play();
    if (p && p.catch) p.catch(function () {});
  }

  // Every click in FRITS loads a new page, which would cut the music off each time.
  // So FRITS remembers in this browser tab which track plays and where, and carries on
  // after the page change. Pressing ♪ Musik again, switching sound off or Feierabend
  // ends that wish. sessionStorage is forgotten when the tab closes.
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

  var music = {
    audio: null,
    track: null,
    play: function (name, from, fadeMs) {
      if (!enabled()) return;
      if (this.audio && this.track === name) return;
      this.stop(true);
      var a = new Audio(base() + "music/" + name + ".mp3");
      a.loop = true;
      a.volume = 0;
      if (from) a.currentTime = from;
      this.audio = a;
      this.track = name;
      saveWish(name, from);
      var p = a.play();
      if (p && p.catch) p.catch(function () { waitForClick(a); });
      fade(a, MUSIC_VOLUME, fadeMs || FADE_MS);
      body().dispatchEvent(new CustomEvent("frits-music", { bubbles: true, detail: { playing: name } }));
    },
    stop: function (switching) {
      var a = this.audio;
      if (!switching) saveWish(null);
      if (!a) return;
      this.audio = null;
      this.track = null;
      fade(a, 0, FADE_MS / 2, function () { a.pause(); });
      body().dispatchEvent(new CustomEvent("frits-music", { bubbles: true, detail: { playing: null } }));
    },
    toggle: function (name) {
      if (this.audio && this.track === name) this.stop(); else this.play(name);
    },
    playing: function () { return this.track; }
  };

  // Some browsers (Safari) only allow sound after a click on this very page.
  // Then the music waits quietly and starts with the first click or key press.
  function waitForClick(a) {
    function go() {
      document.removeEventListener("pointerdown", go, true);
      document.removeEventListener("keydown", go, true);
      if (music.audio === a) { var p = a.play(); if (p && p.catch) p.catch(function () {}); }
    }
    document.addEventListener("pointerdown", go, true);
    document.addEventListener("keydown", go, true);
  }

  window.addEventListener("pagehide", function () {
    if (music.audio) saveWish(music.track, music.audio.currentTime);
  });

  function resume() {
    var wish = loadWish();
    if (!wish || !wish.track || !enabled()) return;
    if (document.querySelector(".feierabend-page")) { saveWish(null); return; }
    // The page's own music button decides: the Steuer-Quest switches to its adventure track,
    // and leaving it switches back to the chosen mood.
    var main = document.querySelector("[data-frits-music-main]");
    var track = main ? main.getAttribute("data-frits-music") : wish.track;
    music.play(track, track === wish.track ? wish.time : 0, 800);
  }

  function setEnabled(on) {
    body().setAttribute("data-frits-sound", on ? "on" : "off");
    if (!on) music.stop();
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-frits-cue],[data-frits-music]");
    if (!el) return;
    if (el.hasAttribute("data-frits-cue")) cue(el.getAttribute("data-frits-cue"));
    if (el.hasAttribute("data-frits-music")) music.toggle(el.getAttribute("data-frits-music"));
  });

  // Music buttons show whether their track is on (aria-pressed). A button with
  // data-label-playing="♪ Musik läuft" also swaps to that text while it plays.
  document.addEventListener("frits-music", function (e) {
    var playing = e.detail && e.detail.playing;
    document.querySelectorAll("[data-frits-music]").forEach(function (el) {
      var on = el.getAttribute("data-frits-music") === playing;
      el.setAttribute("aria-pressed", on ? "true" : "false");
      var label = el.getAttribute("data-label-playing");
      if (!label) return;
      if (!el.hasAttribute("data-label")) el.setAttribute("data-label", el.textContent);
      el.textContent = on ? label : el.getAttribute("data-label");
    });
  });

  // htmx turns the HX-Trigger response header into an event on <body>.
  document.addEventListener("frits-cue", function (e) {
    var d = e.detail;
    cue(typeof d === "string" ? d : d && (d.value || d.name));
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", resume);
  else resume();

  window.FritsSound = { cue: cue, music: music, setEnabled: setEnabled, enabled: enabled };
})();

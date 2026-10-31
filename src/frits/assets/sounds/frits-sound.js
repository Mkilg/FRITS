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
 * Music fades in and out slowly and never starts by itself.
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

  function fade(audio, to, ms, done) {
    var from = audio.volume, start = performance.now();
    function step(now) {
      var k = Math.min(1, (now - start) / ms);
      audio.volume = from + (to - from) * k;
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

  var music = {
    audio: null,
    track: null,
    play: function (name) {
      if (!enabled()) return;
      if (this.audio && this.track === name) return;
      this.stop();
      var a = new Audio(base() + "music/" + name + ".mp3");
      a.loop = true;
      a.volume = 0;
      this.audio = a;
      this.track = name;
      var p = a.play();
      if (p && p.catch) p.catch(function () {});
      fade(a, MUSIC_VOLUME, FADE_MS);
      body().dispatchEvent(new CustomEvent("frits-music", { detail: { playing: name } }));
    },
    stop: function () {
      var a = this.audio;
      if (!a) return;
      this.audio = null;
      this.track = null;
      fade(a, 0, FADE_MS / 2, function () { a.pause(); });
      body().dispatchEvent(new CustomEvent("frits-music", { detail: { playing: null } }));
    },
    toggle: function (name) {
      if (this.audio && this.track === name) this.stop(); else this.play(name);
    },
    playing: function () { return this.track; }
  };

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

  // htmx turns the HX-Trigger response header into an event on <body>.
  document.addEventListener("frits-cue", function (e) {
    var d = e.detail;
    cue(typeof d === "string" ? d : d && (d.value || d.name));
  });

  window.FritsSound = { cue: cue, music: music, setEnabled: setEnabled, enabled: enabled };
})();

/* Opening one of Fritte's chests.
   The page already shows what was inside, so it works without JavaScript and with reduced
   motion. Only right after opening (data-neu) the lid goes up once: "Ruhig" opens it gently,
   "Verspielt" lets the chest wobble first, then the find jumps out with a little tune.
   Reloading shows the same find: it was decided once, on the server. */
(function () {
  "use strict";
  var root = document.querySelector("[data-truhe]");
  if (!root || !root.hasAttribute("data-neu")) return;
  // A reload or the back button shows the find right away, without the show.
  // The other finds of "Alle öffnen" (mit=...) stay in the address, only the show goes.
  try {
    var mit = new URLSearchParams(location.search).get("mit");
    history.replaceState(history.state, "", location.pathname + (mit ? "?mit=" + encodeURIComponent(mit) : ""));
  } catch (e) { /* egal */ }
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var stufe = root.getAttribute("data-seltenheit");
  var skip = root.querySelector("[data-truhe-skip]");
  var timers = [];

  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function klang() {
    var s = window.FritsSound;
    if (s && s.enabled && s.enabled() && s.truhe) s.truhe(stufe);
  }
  function finish() {
    timers.forEach(clearTimeout);
    timers = [];
    root.classList.remove("zu", "wackelt");
    document.body.classList.remove("truhe-verdeckt");
    if (stufe !== "gewoehnlich" && playful) root.classList.add("funkelt"); // only "Verspielt" keeps moving
    if (skip) skip.hidden = true;
  }

  if (reduced) { klang(); return; }
  var playful = document.body.getAttribute("data-effekte") === "verspielt";
  root.classList.add("zu");
  document.body.classList.add("truhe-verdeckt"); // e.g. a new sticker would tell too early
  void root.offsetWidth; // closed first, then the lid may move
  if (skip) { skip.hidden = false; skip.addEventListener("click", finish); }
  if (playful) {
    later(function () { root.classList.add("wackelt"); }, 250);
    if (stufe === "legendaer") later(function () { root.classList.remove("wackelt"); void root.offsetWidth; root.classList.add("wackelt"); }, 1150);
    var auf = stufe === "legendaer" ? 2100 : 1250;
    later(klang, auf - 380);
    later(finish, auf);
  } else {
    later(klang, 300);
    later(finish, 650);
  }
})();

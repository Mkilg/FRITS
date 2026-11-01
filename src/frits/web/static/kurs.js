/* "So funktionieren KI-Agenten": everything to click in the course bites.
   Without JavaScript every panel and every answer is simply shown, so nothing is lost. */
(function () {
  "use strict";

  var root = document.querySelector("[data-kurs]");
  if (!root) return;
  root.classList.add("kurs-js");

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function own(group, selector, attr) {
    // Only the elements that belong to this group, not to a group inside it
    return Array.prototype.filter.call(group.querySelectorAll(selector), function (el) {
      return el.parentElement.closest(attr) === group;
    });
  }
  function calm() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function joy(stamped) {
    // Sound only if Fritz turned it on (Einstellungen › Klang), same as everywhere in FRITS
    if (window.Frits && window.Frits.feedback) window.Frits.feedback("done", !!stamped);
  }

  // ---------- Pick one, see its panel (tabs, stairs, modes, the Codex table ...) ----------
  each(document.querySelectorAll("[data-auswahl]"), function (group) {
    var buttons = own(group, "[data-zeige]", "[data-auswahl]");
    var panels = own(group, "[data-tafel]", "[data-auswahl]");
    var keys = buttons.map(function (b) { return b.getAttribute("data-zeige"); });
    function show(key) {
      buttons.forEach(function (b) {
        var on = b.getAttribute("data-zeige") === key;
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.classList.toggle("an", on);
      });
      panels.forEach(function (p) { p.classList.toggle("offen", p.getAttribute("data-tafel") === key); });
      group.setAttribute("data-gewaehlt", key);
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () { show(b.getAttribute("data-zeige")); });
    });
    var start = group.getAttribute("data-start");
    if (keys.length) show(keys.indexOf(start) >= 0 ? start : keys[0]);
    var next = group.querySelector("[data-naechste]");
    if (next) {
      next.addEventListener("click", function () {
        var i = keys.indexOf(group.getAttribute("data-gewaehlt"));
        show(keys[(i + 1) % keys.length]);
        if (i + 1 === keys.length - 1) next.textContent = "Nochmal von vorn ↺";
        else if (i + 1 === keys.length) next.textContent = "Nächste Station →";
      });
    }
  });

  // ---------- Little quizzes: the first try counts, trying again is always fine ----------
  each(document.querySelectorAll("[data-quiz]"), function (quiz) {
    var fragen = quiz.querySelectorAll("[data-frage]");
    var stand = quiz.querySelector("[data-quiz-stand]");
    var richtig = 0, beantwortet = 0;
    each(fragen, function (frage) {
      var erster = true;
      var meldung = frage.querySelector(".rueckmeldung");
      var antworten = frage.querySelectorAll("[data-antwort]");
      each(antworten, function (b) {
        b.addEventListener("click", function () {
          var ok = b.hasAttribute("data-richtig");
          each(antworten, function (x) { x.classList.remove("gewaehlt-richtig", "gewaehlt-falsch"); });
          b.classList.add(ok ? "gewaehlt-richtig" : "gewaehlt-falsch");
          meldung.textContent = (ok ? "✓ " : "Hmm. ") + b.getAttribute("data-sagt");
          meldung.className = "rueckmeldung " + (ok ? "gut-text" : "schlecht-text");
          if (!erster) return;
          erster = false;
          beantwortet += 1;
          if (ok) richtig += 1;
          frage.classList.add("beantwortet");
          if (beantwortet === fragen.length && stand) {
            stand.textContent = richtig === fragen.length
              ? (fragen.length > 1 ? "🎉 Alle richtig beim ersten Versuch!" : "🎉 Richtig!")
              : richtig + " von " + fragen.length + " beim ersten Versuch richtig. Gut gemacht!";
            stand.classList.add("fertig");
            joy(false);
          }
        });
      });
    });
  });

  // ---------- The blacking-out game ----------
  each(document.querySelectorAll("[data-schwaerzspiel]"), function (spiel) {
    var woerter = spiel.querySelectorAll("[data-wort]");
    var ergebnis = spiel.querySelector("[data-ergebnis]");
    each(woerter, function (w) {
      w.addEventListener("click", function () {
        w.setAttribute("aria-pressed", w.getAttribute("aria-pressed") === "true" ? "false" : "true");
        w.classList.remove("uebersehen", "zuviel", "treffer");
      });
    });
    function pruefen() {
      var geheim = 0, gefunden = 0, uebersehen = [], zuviel = [];
      each(woerter, function (w) {
        var schwarz = w.getAttribute("aria-pressed") === "true";
        var soll = w.hasAttribute("data-geheim");
        w.classList.remove("uebersehen", "zuviel", "treffer");
        if (soll) geheim += 1;
        if (soll && schwarz) { gefunden += 1; w.classList.add("treffer"); }
        if (soll && !schwarz) { uebersehen.push(w); w.classList.add("uebersehen"); }
        if (!soll && schwarz) { zuviel.push(w); w.classList.add("zuviel"); }
      });
      ergebnis.textContent = "";
      var kopf = document.createElement("p");
      kopf.className = "ergebnis-kopf";
      if (!uebersehen.length && !zuviel.length) {
        kopf.textContent = "🎉 Perfekt! Alle " + geheim + " erwischt, und nichts zu viel.";
        joy(true);
      } else {
        kopf.textContent = "Du hast " + gefunden + " von " + geheim + " erwischt.";
      }
      ergebnis.appendChild(kopf);
      function liste(titel, items) {
        if (!items.length) return;
        var p = document.createElement("p");
        p.textContent = titel;
        var ul = document.createElement("ul");
        ul.className = "plain kurz";
        items.forEach(function (w) {
          var li = document.createElement("li");
          var b = document.createElement("strong");
          b.textContent = w.textContent;
          li.appendChild(b);
          li.appendChild(document.createTextNode(": " + w.getAttribute("data-warum")));
          ul.appendChild(li);
        });
        ergebnis.appendChild(p);
        ergebnis.appendChild(ul);
      }
      liste("Noch zu sehen (rot umrandet):", uebersehen);
      liste("Das darf bleiben (gelb umrandet):", zuviel);
    }
    spiel.querySelector("[data-pruefen]").addEventListener("click", pruefen);
    spiel.querySelector("[data-aufloesen]").addEventListener("click", function () {
      each(woerter, function (w) { w.setAttribute("aria-pressed", w.hasAttribute("data-geheim") ? "true" : "false"); });
      pruefen();
      ergebnis.firstChild.textContent = "So wäre es richtig. Probier es gern noch einmal selbst.";
    });
  });

  // ---------- Play a round, line by line ----------
  each(document.querySelectorAll("[data-abspielen]"), function (box) {
    var spalten = box.querySelectorAll(".protokoll");
    var knopf = box.querySelector("[data-abspielen-start]");
    var timers = [];
    each(spalten, function (ol) { each(ol.children, function (li) { li.classList.add("wartet"); }); });
    knopf.addEventListener("click", function () {
      timers.forEach(clearTimeout);
      timers = [];
      var most = 0;
      each(spalten, function (ol) {
        most = Math.max(most, ol.children.length);
        each(ol.children, function (li) { li.classList.add("wartet"); });
      });
      for (var i = 0; i < most; i += 1) {
        (function (i) {
          timers.push(setTimeout(function () {
            each(spalten, function (ol) { if (ol.children[i]) ol.children[i].classList.remove("wartet"); });
          }, calm() ? 0 : 250 + i * 900));
        })(i);
      }
      knopf.textContent = "↺ Nochmal abspielen";
    });
  });

  // ---------- Switches ----------
  each(document.querySelectorAll("[data-schalter]"), function (s) {
    var ziel = s.classList.contains("umgekehrt") ? "false" : "true";
    var lob = (s.closest("li") || s.closest(".einstellung-mock")).querySelector(".schalter-lob");
    s.addEventListener("click", function () {
      var jetzt = s.getAttribute("aria-pressed") === "true" ? "false" : "true";
      s.setAttribute("aria-pressed", jetzt);
      if (lob) lob.hidden = jetzt !== ziel;
      if (jetzt === ziel) joy(false);
    });
  });

  // ---------- Put the steps in order ----------
  each(document.querySelectorAll("[data-reihenfolge]"), function (box) {
    var knoepfe = box.querySelectorAll(".reihen-knopf");
    var meldung = box.querySelector("[data-reihen-meldung]");
    var loesung = box.querySelector("[data-reihen-loesung]");
    var naechster = 1, fehler = 0;
    each(knoepfe, function (b) {
      b.addEventListener("click", function () {
        if (b.disabled) return;
        if (Number(b.getAttribute("data-pos")) === naechster) {
          b.disabled = true;
          b.classList.add("richtig");
          b.querySelector(".reihen-nr").textContent = naechster;
          naechster += 1;
          fehler = 0;
          meldung.className = "rueckmeldung gut-text";
          if (naechster > knoepfe.length) {
            meldung.textContent = "🎉 Genau so! Das ist das Rezept für jeden Papierkram.";
            loesung.classList.add("offen");
            joy(true);
          } else {
            meldung.textContent = "✓ Richtig. Und dann?";
          }
        } else {
          fehler += 1;
          b.classList.remove("wackeln");
          void b.offsetWidth; // start the little shake again
          b.classList.add("wackeln");
          meldung.className = "rueckmeldung schlecht-text";
          meldung.textContent = "Hmm, nicht ganz. Was kommt als Schritt " + naechster + "?";
          if (fehler >= 2) {
            each(knoepfe, function (x) {
              x.classList.toggle("tipp", Number(x.getAttribute("data-pos")) === naechster);
            });
            meldung.textContent += " Ich hab dir einen Tipp markiert.";
          }
        }
        each(knoepfe, function (x) { if (x.disabled) x.classList.remove("tipp"); });
      });
    });
  });

  // ---------- The cheat sheet: all ticked, stamp! ----------
  each(document.querySelectorAll("[data-spickzettel]"), function (box) {
    var haken = box.querySelectorAll("input[type=checkbox]");
    var fertig = box.querySelector("[data-spick-fertig]");
    var war = false;
    each(haken, function (h) {
      h.addEventListener("change", function () {
        var alle = Array.prototype.every.call(haken, function (x) { return x.checked; });
        fertig.hidden = !alle;
        if (alle && !war) joy(true);
        war = alle;
      });
    });
  });
})();

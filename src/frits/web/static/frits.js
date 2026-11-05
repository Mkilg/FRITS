/* FRITS: a few small helpers. Every page also works without JavaScript. */
(function () {
  "use strict";

  // Ask before steps that can't be undone (numbering an invoice, Storno, discarding), on a
  // card in FRITS's own look instead of the browser's grey box.
  // Put the cursor in a field. A page that arrives in the still hidden Klangrahmen loses its
  // focus when the frame turns visible, so try once more after the frame has shown it.
  function cursorIn(field, pick) {
    function go() {
      field.focus();
      if (pick && field.select) field.select();
    }
    go();
    window.addEventListener("load", function () {
      setTimeout(function () {
        var now = document.activeElement;
        if (now === field || (now && now !== document.body)) return;  // Fritz already moved on
        go();
      }, 80);
    });
  }

  function rueckfrage(message, ja, done, gefahr) {
    if (typeof HTMLDialogElement !== "function") { done(window.confirm(message)); return; }
    var d = document.createElement("dialog");
    d.className = "rueckfrage-karte";
    var p = document.createElement("p");
    p.textContent = message;
    var row = document.createElement("div");
    row.className = "row";
    var yes = document.createElement("button");
    yes.type = "button"; yes.className = gefahr ? "gefahr" : "primary"; yes.textContent = ja || "Ja";
    var no = document.createElement("button");
    no.type = "button"; no.className = "quiet"; no.textContent = "Noch nicht";
    row.appendChild(yes); row.appendChild(no);
    d.appendChild(p); d.appendChild(row);
    document.body.appendChild(d);
    var answered = false;
    function answer(ok) {
      if (answered) return;
      answered = true;
      d.close();
      d.remove();
      done(ok);
    }
    yes.addEventListener("click", function () { answer(true); });
    no.addEventListener("click", function () { answer(false); });
    d.addEventListener("cancel", function () { answer(false); });
    d.showModal();
    yes.focus();
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    var button = e.submitter;
    var asker = button && button.hasAttribute("data-confirm") ? button : form;
    var message = asker.getAttribute("data-confirm");
    if (message && form.getAttribute("data-bestaetigt") !== "1") {
      e.preventDefault();
      // A step that undoes something (Stornieren, Löschen) gets a red yes, not the gold one.
      var gefahr = (button && button.classList.contains("danger")) || !!form.querySelector("button.danger");
      rueckfrage(message, asker.getAttribute("data-confirm-ja"), function (ok) {
        if (!ok) return;
        form.setAttribute("data-bestaetigt", "1");
        if (form.requestSubmit) form.requestSubmit(button || undefined);
        else form.submit();
      }, gefahr);
      return;
    }
    form.removeAttribute("data-bestaetigt");
    var busy = (button && button.getAttribute("data-busy")) || form.getAttribute("data-busy");
    if (busy) {
      // A disabled button's name and value are not sent, so keep which one was pressed.
      if (button && button.name) {
        var keep = document.createElement("input");
        keep.type = "hidden";
        keep.name = button.name;
        keep.value = button.value;
        form.appendChild(keep);
      }
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

  // ---------- Einwurf: hand FRITS something without a folder ----------
  // Drag files onto the page, press ⌘V to paste what was copied (photo, PDF, text from a mail),
  // or pick a file. Files are sent right away; text opens in the box first, so Fritz sees what
  // he pasted (a password in the clipboard must never travel on by accident).
  var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
  function zeigeTasten() {
    if (mac) return;
    document.querySelectorAll("[data-taste-cmd]").forEach(function (k) { k.textContent = "Strg"; });
  }

  document.addEventListener("change", function (e) {
    var input = e.target;
    if (input.matches && input.matches("[data-autosubmit]") && input.files && input.files.length) {
      input.form.requestSubmit ? input.form.requestSubmit() : input.form.submit();
    }
  });

  function einwurfFuer(el) {
    var own = el && el.closest && el.closest("form[data-einwurf]");
    return own || document.querySelector("form[data-einwurf][data-einwurf-haupt]") ||
      document.querySelector("form[data-einwurf]");
  }
  function dropzoneIn(form) { return form && form.querySelector("[data-dropzone]"); }

  function stamp() {
    var d = new Date(), z = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate()) + "-" + z(d.getHours()) + z(d.getMinutes());
  }
  var TYPEN = { "image/png": ".png", "image/jpeg": ".jpg", "image/heic": ".heic", "application/pdf": ".pdf",
    "text/plain": ".txt" };
  // A screenshot pasted from the clipboard is called "image.png": give it a name Fritz recognises.
  function benannt(file, i) {
    var generic = !file.name || /^(image|bild|unbenannt|untitled)(\.\w+)?$/i.test(file.name);
    if (!generic) return file;
    var ext = TYPEN[file.type] || (file.name.match(/\.\w+$/) || [".png"])[0];
    var name = "eingefuegt-" + stamp() + (i ? "-" + (i + 1) : "") + ext;
    try { return new File([file], name, { type: file.type }); } catch (err) { return file; }
  }

  function dateienGeben(form, files) {
    var input = form && form.querySelector("input[type=file]");
    if (!input || !files.length) return false;
    try {
      var dt = new DataTransfer();
      files.forEach(function (f, i) { dt.items.add(benannt(f, i)); });
      input.files = dt.files;
    } catch (err) {
      try { input.files = files; } catch (err2) { return false; }
    }
    var zone = dropzoneIn(form);
    if (zone) zone.classList.add("bringt");
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function textGeben(form, text) {
    var box = form && form.querySelector("textarea[name=text]");
    if (!box || !text.trim()) return false;
    var details = box.closest("details");
    if (details) details.open = true;
    box.value = box.value.trim() ? box.value.replace(/\s+$/, "") + "\n\n" + text : text;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    if (box.scrollIntoView) box.scrollIntoView({ block: "center", behavior: "smooth" });
    return true;
  }

  function hinweis(form, text) {
    var old = form.querySelector(".dz-hinweis");
    if (old) old.remove();
    var p = document.createElement("p");
    p.className = "dz-hinweis";
    p.setAttribute("role", "status");
    p.textContent = text;
    form.appendChild(p);
  }

  function dateienAus(data) {
    var files = Array.prototype.slice.call((data && data.files) || []);
    if (!files.length && data && data.items) {
      Array.prototype.forEach.call(data.items, function (item) {
        if (item.kind === "file") { var f = item.getAsFile(); if (f) files.push(f); }
      });
    }
    return files;
  }

  // In some browsers, a file copied in Finder pastes as a picture of its icon plus its name.
  function nurDateiSymbol(files, text) {
    var name = (text || "").trim();
    return files.length === 1 && /^image\//.test(files[0].type) && /^[^\n\/]+\.(pdf|docx?|eml|txt|md|xml|pages)$/i.test(name);
  }

  function editierbar(el) {
    if (!el || !el.closest) return false;
    return !!el.closest("input, textarea, select, [contenteditable=''], [contenteditable=true]");
  }

  document.addEventListener("paste", function (e) {
    var form = einwurfFuer(e.target);
    if (!form) return;
    var data = e.clipboardData;
    var files = dateienAus(data);
    var text = data ? data.getData("text/plain") : "";
    if (files.length) {
      if (nurDateiSymbol(files, text)) {
        e.preventDefault();
        hinweis(form, "Kopierte Dateien aus dem Finder kommen hier nur als Bildchen an. Zieh die Datei bitte direkt auf das Feld.");
        return;
      }
      e.preventDefault();
      dateienGeben(form, files);
      return;
    }
    // Text pasted into a field stays in that field, as everywhere else.
    if (editierbar(e.target) || !text) return;
    e.preventDefault();
    textGeben(form, text);
  });

  // ⌘↩ (Strg+↩) in the text box sends it.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey)) return;
    var box = e.target.closest && e.target.closest("form[data-einwurf] textarea[name=text], form[data-liste] textarea");
    if (!box || !box.value.trim()) return;
    e.preventDefault();
    box.form.requestSubmit ? box.form.requestSubmit() : box.form.submit();
  });

  // ⌘S (Strg+S) saves the form that has a "Speichern" button, e.g. a draft, instead of
  // making the browser save the web page as a file.
  document.addEventListener("keydown", function (e) {
    if (!(e.metaKey || e.ctrlKey) || e.altKey || (e.key !== "s" && e.key !== "S")) return;
    var save = document.querySelector("form button[name=action][value=save]");
    if (!save) return;
    e.preventDefault();
    save.click();
  });

  function zoneOf(e) { return e.target.closest && e.target.closest("[data-dropzone]"); }
  function hatDateien(e) {
    var types = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.types) || []);
    return types.indexOf("Files") >= 0 || types.indexOf("text/plain") >= 0;
  }
  ["dragenter", "dragover"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      if (!document.querySelector("[data-dropzone]") || !hatDateien(e)) return;
      e.preventDefault();  // a file dropped next to the field must not open in the browser
      var zone = zoneOf(e) || dropzoneIn(einwurfFuer(null)) || document.querySelector("[data-dropzone]");
      document.querySelectorAll("[data-dropzone].over").forEach(function (z) {
        if (z !== zone) z.classList.remove("over");
      });
      if (zone) zone.classList.add("over");
      document.body.classList.add("zieht-datei");
    });
  });
  function dragEnde() {
    document.body.classList.remove("zieht-datei");
    document.querySelectorAll("[data-dropzone].over").forEach(function (z) { z.classList.remove("over"); });
  }
  document.addEventListener("dragleave", function (e) {
    if (!e.relatedTarget) dragEnde();  // left the window
    var zone = zoneOf(e);
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove("over");
  });
  document.addEventListener("drop", function (e) {
    if (!document.querySelector("[data-dropzone]")) return;
    // Places with their own drag & drop (e.g. the Steuer-Quest cards) handle it themselves.
    if (e.defaultPrevented || (e.target.closest && e.target.closest("[data-eigenes-ziehen]"))) { dragEnde(); return; }
    var files = dateienAus(e.dataTransfer);
    // Text dragged into a text field lands there, as everywhere else.
    if (!files.length && e.target.closest && e.target.closest("textarea, input:not([type=file])")) { dragEnde(); return; }
    e.preventDefault();
    dragEnde();
    var zone = zoneOf(e);
    var form = zone ? zone.closest("form") : einwurfFuer(null);
    if (!zone && !form) zone = document.querySelector("[data-dropzone]");
    form = form || (zone && zone.closest("form"));
    if (files.length) {
      if (form && form.hasAttribute("data-einwurf")) { dateienGeben(form, files); return; }
      var input = zone && zone.querySelector("input[type=file]");
      if (!input) return;
      try { input.files = e.dataTransfer.files; } catch (err) { return; }
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    var text = e.dataTransfer && e.dataTransfer.getData("text/plain");
    if (text && form) textGeben(form, text);
  });

  // "Mehr" in the top row closes again when you click anywhere else.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.mehr[open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });
  // ...and opens towards the side where there is room, so it never sticks out of a narrow window.
  document.querySelectorAll("details.mehr").forEach(function (d) {
    d.addEventListener("toggle", function () {
      var list = d.querySelector(".mehr-liste");
      if (!d.open || !list) return;
      list.classList.remove("nach-rechts");
      if (list.getBoundingClientRect().left < 4) list.classList.add("nach-rechts");
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
    // With sound on, the player keeps one unlocked AudioContext for the whole visit
    // (frits-sound.js), so the plopp also sounds right after a page change.
    if (window.FritsSound && window.FritsSound.stamp) { window.FritsSound.stamp(); return; }
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
    if (cue === "rechnung") {  // the big moment: stamp, then the till rings, in both modes
      setTimeout(stampSound, 300);
      setTimeout(function () { if (sound.ching) sound.ching(); else sound.cue("done"); }, 520);
      return;
    }
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
    zeigeTasten();
    if (playful() && !calmMotion()) document.querySelectorAll("[data-zaehlen]").forEach(countUp);
  }
  // Settings: try "Ruhig" and "Verspielt" before saving. The preview card plays the chosen kind.
  function preview() {
    var box = document.querySelector("[data-vorschau]");
    if (!box) return;
    var chosen = document.querySelector('input[name="effects"]:checked');
    var fresh = box.cloneNode(true);  // a fresh copy starts its animations again
    fresh.setAttribute("data-effekte", chosen ? chosen.value : "ruhig");
    box.parentNode.replaceChild(fresh, box);
    if (document.body.getAttribute("data-frits-sound") === "on") setTimeout(stampSound, 150);
    if (fresh.getAttribute("data-effekte") === "verspielt" && !calmMotion()) {
      fresh.querySelectorAll("[data-vorschau-zahl]").forEach(countUp);
    }
  }
  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-vorschau-start]")) preview();
  });
  document.addEventListener("change", function (event) {
    if (event.target.name === "effects") preview();
  });
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
      } else if (window.FritsSound && window.FritsSound.follow) {
        window.FritsSound.follow();
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

  // ---------- Small steps without a page change (Deine Liste) ----------
  // Ticking a point off sends the form in the background and swaps in the new page content,
  // so the music keeps playing and the page stays where it was. Without JavaScript, or if
  // anything goes wrong, the form is simply sent the normal way.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute("data-leise") || e.defaultPrevented || !window.fetch || !window.DOMParser) return;
    e.preventDefault();
    form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, "text/html");
        var main = next.querySelector("main"), here = document.querySelector("main");
        if (!main || !here) throw new Error("no main");
        // The note ("Erledigt: …") goes where the click was, not to the top of the page.
        var place = form.closest("section[id]");
        var there = place && main.querySelector("#" + place.id);
        var notes = main.querySelectorAll(".flash");
        if (there) {
          var after = there.firstElementChild;
          Array.prototype.forEach.call(notes, function (n) {
            n.classList.add("flash-hier");
            there.insertBefore(n, after ? after.nextSibling : null);
          });
        }
        var y = window.scrollY;
        here.replaceWith(document.adoptNode(main));
        window.scrollTo(0, y);
        start();
        var cue = main.getAttribute("data-cue");
        if (cue) feedback(cue, false);
      })
      .catch(function () {
        form.removeAttribute("data-leise");
        form.submit();
      });
  });

  // A form with data-merken keeps what Fritz typed if the server says "bitte prüfen":
  // saved on submit, put back only when the next page shows an error, then forgotten.
  (function () {
    var error = document.querySelector(".flash.error");
    Array.prototype.forEach.call(document.querySelectorAll("form[data-merken]"), function (form) {
      var key = "frits-form-" + form.getAttribute("data-merken");
      var saved = null;
      try { saved = JSON.parse(sessionStorage.getItem(key) || "null"); sessionStorage.removeItem(key); } catch (e) { saved = null; }
      // Fields are matched by name and their place among fields of that name, so a button
      // that belongs to the form from elsewhere (the note's "trotzdem nachtragen") moves nothing.
      function places(each) {
        var seen = {};
        Array.prototype.forEach.call(form.elements, function (field) {
          if (!field.name) return;
          seen[field.name] = (seen[field.name] || 0) + 1;
          each(field, field.name + "#" + seen[field.name]);
        });
      }
      if (error && saved) {
        places(function (field, i) {
          var was = saved[i];
          if (!was || was.n !== field.name) return;
          if (field.type === "radio" || field.type === "checkbox") field.checked = was.c;
          else field.value = was.v;
        });
        var box = form.closest("details");
        if (box) box.open = true;
      }
      // Point at the field the note is about: red frame, cursor in it, number selected.
      var feld = error && error.getAttribute("data-feld");
      // "quantity:2" is the third field of that name (a row in the draft's table).
      var parts = feld ? feld.split(":") : [];
      var bad = parts.length === 2
        ? form.querySelectorAll('[name="' + parts[0] + '"]')[Number(parts[1])]
        : feld && form.elements.namedItem(feld);
      // The note goes where Fritz is looking, not to the top of a long page (Marie: "Ich
      // drücke Nachtragen, und nichts passiert"): under the field it is about, or else right
      // above the button he just pressed.
      if (error && saved) {
        var anchor = bad && bad.closest ? bad.closest("fieldset, label") : null;
        var actions = form.querySelector(".actions");
        if (anchor && form.contains(anchor)) {
          error.classList.add("flash-hier");
          anchor.parentNode.insertBefore(error, anchor.nextSibling);
        } else if (actions) {
          error.classList.add("flash-hier");
          actions.parentNode.insertBefore(error, actions);
        }
        if (!bad && form.contains(error)) error.scrollIntoView({ block: "center" });
      }
      if (bad && bad.focus) {
        bad.classList.add("falsch");
        bad.setAttribute("aria-invalid", "true");
        var open = bad.closest("details");
        if (open) open.open = true;
        cursorIn(bad, true);
        if (error && form.contains(error)) {
          window.addEventListener("load", function () {
            setTimeout(function () { error.scrollIntoView({ block: "nearest" }); }, 120);
          });
        }
        bad.addEventListener("input", function () {
          bad.classList.remove("falsch");
          bad.removeAttribute("aria-invalid");
        }, { once: true });
      }
      form.addEventListener("submit", function () {
        var data = {};
        places(function (field, i) {
          if (/^(file|hidden|password|submit|button)$/.test(field.type)) return;
          data[i] = { n: field.name, v: field.value, c: field.checked };
        });
        try { sessionStorage.setItem(key, JSON.stringify(data)); } catch (e) { /* egal */ }
      });
    });
  })();

  // Browsers skip autofocus inside the app's frame: put the cursor there by hand, unless an
  // error note already points at another field.
  (function () {
    var first = document.querySelector("[autofocus]");
    if (!first || document.querySelector(".flash.error[data-feld]")) return;
    cursorIn(first, false);
  })();

  // The browser's own "Please fill out this field." is English: say it in German, plainly.
  document.addEventListener("invalid", function (e) {
    var f = e.target;
    if (!f || !f.validity || f.validity.customError) return;
    var text = "Bitte ausfüllen.";
    if (f.validity.tooShort) text = "Bitte mindestens " + f.getAttribute("minlength") + " Zeichen.";
    else if (f.validity.typeMismatch && f.type === "email") text = "Bitte eine E-Mail-Adresse wie name@beispiel.de.";
    else if (f.validity.rangeOverflow || f.validity.rangeUnderflow) text = "Diese Zahl passt hier nicht.";
    else if (f.validity.badInput || f.validity.patternMismatch) text = "Das passt so nicht. Schau bitte noch einmal.";
    f.setCustomValidity(text);
  }, true);
  document.addEventListener("input", function (e) {
    if (e.target && e.target.setCustomValidity) e.target.setCustomValidity("");
  }, true);
  document.addEventListener("change", function (e) {
    if (e.target && e.target.setCustomValidity) e.target.setCustomValidity("");
  }, true);

  // Folded sections ("Fächer" in the settings): a link like /settings#ki opens its fold,
  // and a field the browser complains about is never hidden in a closed one.
  // A note that was just put next to its field wins over the page's anchor: scrolling to
  // "#frueher" would push it out of sight again.
  function noteInSight() {
    var hier = document.querySelector("form .flash.error.flash-hier");
    if (!hier) return false;
    var outer = hier.closest("details");
    if (outer && !outer.open) outer.open = true;
    hier.scrollIntoView({ block: "center" });
    return true;
  }
  function openFold() {
    var id = decodeURIComponent((location.hash || "").slice(1));
    var target = id && document.getElementById(id);
    if (!target) return;
    var fold = target.tagName === "DETAILS" ? target : target.closest("details");
    if (fold && !fold.open) fold.open = true;
    if (target.scrollIntoView) target.scrollIntoView({ block: "start" });
  }
  // "Schon bezahlt? Ja, am …": with "Ja" the day is needed, the browser says so at the field
  // before anything is sent; with "Nein" it isn't.
  Array.prototype.forEach.call(document.querySelectorAll("fieldset.schon-bezahlt"), function (box) {
    var ja = box.querySelector('input[name="bezahlt"][value="ja"]');
    var day = box.querySelector('input[name="paid"]');
    if (!ja || !day) return;
    function sync() { day.required = ja.checked; }
    box.addEventListener("change", sync);
    sync();
  });
  if (!noteInSight()) openFold();
  window.addEventListener("hashchange", openFold);
  document.addEventListener("invalid", function (e) {
    var fold = e.target && e.target.closest && e.target.closest("details");
    if (fold) fold.open = true;
  }, true);

  // Info-i (Marie, 3.11.): a tap on the little "i" opens its text, a tap anywhere else or
  // Escape closes it again. Only one is open at a time.
  // On the phone the text is a sheet fixed to the bottom of the screen. A tilted title
  // around the "i" (transform) would pin it to the title instead (Lena Runde 62), so while
  // open the sheet sits directly in <body> (or its <dialog>) and goes back home when it closes.
  function infoText(i) {
    var k = i.querySelector(".info-i-knopf");
    var id = k && k.getAttribute("aria-controls");
    return (id && document.getElementById(id)) || i.querySelector(".info-i-text");
  }
  function infoZu(ausser) {
    document.querySelectorAll(".info-i.offen").forEach(function (i) {
      if (i === ausser) return;
      i.classList.remove("offen");
      var k = i.querySelector(".info-i-knopf");
      if (k) k.setAttribute("aria-expanded", "false");
      var box = infoText(i);
      if (box && box.parentNode !== i) { box.classList.remove("frei"); i.appendChild(box); }
    });
  }
  document.addEventListener("click", function (e) {
    var knopf = e.target.closest && e.target.closest(".info-i-knopf");
    if (!knopf) { if (!(e.target.closest && e.target.closest(".info-i-text"))) infoZu(null); return; }
    e.preventDefault();  // inside a <label> the tap must not tick the box
    e.stopPropagation();
    var info = knopf.parentNode;
    infoZu(info);
    var auf = info.classList.toggle("offen");
    knopf.setAttribute("aria-expanded", auf ? "true" : "false");
    // Next to the edge of the page the box would be cut off (a jar on the left of the
    // shelf), so it moves back inside. On the phone it is a sheet at the bottom anyway.
    var box = infoText(info);
    if (!auf) { infoZu(null); return; }
    if (!box) return;
    box.style.marginLeft = "";
    box.classList.remove("nach-oben");
    if (getComputedStyle(box).position === "fixed") {
      box.classList.add("frei");
      // inside a modal <dialog> it must stay in the dialog, which lies above the page
      (info.closest("dialog") || document.body).appendChild(box);
      return;
    }
    var r = box.getBoundingClientRect(), rand = 8, breit = document.documentElement.clientWidth;
    var dx = r.left < rand ? rand - r.left : (r.right > breit - rand ? breit - rand - r.right : 0);
    if (dx) box.style.marginLeft = dx + "px";
    // At the end of the page there is no room below: it opens upwards.
    var hoch = window.innerHeight || document.documentElement.clientHeight;
    var docUnten = document.documentElement.scrollHeight - window.scrollY;
    if (r.bottom > Math.min(hoch, docUnten) - rand && info.getBoundingClientRect().top > r.height + 2 * rand) {
      box.classList.add("nach-oben");
    }
  });
  // Escape with an "i" open closes only the "i", not the dialog around it (Lena Runde 64):
  // caught first (capture on window), so neither the <dialog> nor steuer.js see it.
  window.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || !document.querySelector(".info-i.offen")) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    infoZu(null);
  }, true);

  // Expense form: while you type "Deutsche Bahn", the category follows, from the same word
  // list as the receipt reader (Lena Runde 69). Once you pick a category yourself, it stays.
  document.querySelectorAll("select[data-raten]").forEach(function (wahl) {
    var muster;
    try {
      muster = JSON.parse(wahl.getAttribute("data-raten")).map(function (m) {
        return [m[0], new RegExp(m[1], "iu")];
      });
    } catch (e) { return; }
    var form = wahl.form, anfang = wahl.value, selbst = false;
    if (!form) return;
    wahl.addEventListener("change", function () { selbst = true; });
    function raten() {
      if (selbst) return;
      var text = ["vendor", "description"].map(function (n) {
        var f = form.elements[n];
        return f ? f.value : "";
      }).join(" ").toLowerCase();
      var treffer = null;
      for (var i = 0; i < muster.length && !treffer; i++) if (muster[i][1].test(text)) treffer = muster[i][0];
      wahl.value = treffer || anfang;
    }
    ["vendor", "description"].forEach(function (n) {
      var f = form.elements[n];
      if (f) f.addEventListener("input", raten);
    });
  });

  window.Frits = { feedback: feedback, stampSound: stampSound, playful: playful };
})();

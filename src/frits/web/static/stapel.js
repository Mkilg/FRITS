/* Stapel: viele Dateien, ganze Ordner und ZIPs auf einmal hineingeben.
   Ohne JavaScript geht alles auch: Dann schickt das Formular alle Dateien in einem Rutsch,
   und "Weiter lesen" liest in größeren Schritten. */
(function () {
  "use strict";

  var PAKET = 15;  // Dateien je Hochlade-Anfrage
  var PAKET_BYTES = 40 * 1024 * 1024;

  function token() {
    var t = document.querySelector("input[name=token]");
    return t ? t.value : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST", body: body, credentials: "same-origin",
      headers: { "X-Frits-Leise": "1" }
    }).then(function (r) {
      if (!r.ok) throw new Error("Fehler " + r.status);
      return r.json();
    });
  }

  function fortschritt(form, wert, max, text) {
    var box = form.querySelector("[data-stapel-fortschritt]");
    if (!box) return;
    box.hidden = false;
    var bar = box.querySelector("progress");
    if (bar) { bar.max = max || 1; bar.value = wert; }
    var t = box.querySelector("[data-stapel-fortschritt-text]");
    if (t && text) t.textContent = text;
  }

  // ---------- Ordner auflösen: aus einem gezogenen Ordner werden seine Dateien ----------

  function eintragLesen(entry, pfad, liste) {
    return new Promise(function (fertig) {
      if (entry.isFile) {
        entry.file(function (f) { liste.push({ datei: f, pfad: pfad + f.name }); fertig(); }, function () { fertig(); });
      } else if (entry.isDirectory) {
        var leser = entry.createReader(), alle = [];
        var weiter = function () {
          leser.readEntries(function (stueck) {
            if (!stueck.length) {
              Promise.all(alle.map(function (e) { return eintragLesen(e, pfad + entry.name + "/", liste); })).then(fertig);
              return;
            }
            alle = alle.concat(Array.prototype.slice.call(stueck));
            weiter();  // readEntries liefert höchstens 100 auf einmal
          }, function () { fertig(); });
        };
        weiter();
      } else { fertig(); }
    });
  }

  function dateienAusDrop(dt) {
    var items = dt && dt.items ? Array.prototype.slice.call(dt.items) : [];
    var eintraege = items.map(function (i) { return i.webkitGetAsEntry ? i.webkitGetAsEntry() : null; }).filter(Boolean);
    if (!eintraege.length) {
      return Promise.resolve(Array.prototype.slice.call((dt && dt.files) || []).map(function (f) { return { datei: f, pfad: f.name }; }));
    }
    var liste = [];
    return Promise.all(eintraege.map(function (e) { return eintragLesen(e, "", liste); })).then(function () { return liste; });
  }

  // ---------- Hochladen in Paketen, mit Fortschritt ----------

  function hochladen(form, liste) {
    liste = liste.filter(function (x) { return x.datei && !/(^|\/)\./.test(x.pfad); });
    if (!liste.length) return;
    var gesamt = liste.length, fertig = 0, id = null, hinweise = [];
    form.classList.add("laedt");
    fortschritt(form, 0, gesamt, "Ich lege " + gesamt + " Datei" + (gesamt > 1 ? "en" : "") + " in den Stapel …");
    var pakete = [], jetzt = [], groesse = 0;
    liste.forEach(function (x) {
      if (jetzt.length && (jetzt.length >= PAKET || groesse + x.datei.size > PAKET_BYTES)) {
        pakete.push(jetzt); jetzt = []; groesse = 0;
      }
      jetzt.push(x); groesse += x.datei.size;
    });
    if (jetzt.length) pakete.push(jetzt);

    var text = form.querySelector("textarea[name=text]");
    function naechstes(i) {
      if (i >= pakete.length) {
        if (hinweise.length) sessionStorageSetzen(hinweise);
        window.location.href = "/stapel/" + id;
        return;
      }
      var body = new FormData();
      body.append("token", token());
      pakete[i].forEach(function (x) { body.append("files", x.datei, x.datei.name); body.append("pfade", x.pfad); });
      if (i === 0 && text && text.value.trim()) body.append("text", text.value);
      post(id ? "/stapel/" + id + "/dazu" : "/stapel/neu", body).then(function (antwort) {
        id = antwort.id;
        hinweise = hinweise.concat(antwort.hinweise || []);
        fertig += pakete[i].length;
        fortschritt(form, fertig, gesamt, fertig + " von " + gesamt + " im Stapel …");
        naechstes(i + 1);
      }).catch(function () {
        fortschritt(form, fertig, gesamt, "Das Hochladen ist hängen geblieben. Was schon da ist, bleibt im Stapel.");
        if (id) window.setTimeout(function () { window.location.href = "/stapel/" + id; }, 1500);
        form.classList.remove("laedt");
      });
    }
    naechstes(0);
  }

  function sessionStorageSetzen(hinweise) {
    try { window.sessionStorage.setItem("stapel-hinweise", JSON.stringify(hinweise.slice(0, 8))); } catch (e) { /* egal */ }
  }

  function hinweiseZeigen() {
    var raw = null;
    try { raw = window.sessionStorage.getItem("stapel-hinweise"); window.sessionStorage.removeItem("stapel-hinweise"); } catch (e) { return; }
    if (!raw) return;
    var main = document.querySelector("main");
    var liste;
    try { liste = JSON.parse(raw); } catch (e) { return; }
    liste.forEach(function (h) {
      var div = document.createElement("div");
      div.className = "flash error";
      div.setAttribute("role", "status");
      div.textContent = h;
      main.insertBefore(div, main.firstChild);
    });
  }

  var einwurf = document.querySelector("form[data-stapel-einwurf]");
  if (einwurf) {
    var knopf = einwurf.querySelector("[data-ohne-js]");
    if (knopf) knopf.hidden = true;  // mit JavaScript geht es gleich nach dem Auswählen los

    // Ziehen: im Capture, damit die allgemeine Einwurf-Hilfe Ordner nicht als leere Datei schickt
    document.addEventListener("drop", function (e) {
      var dt = e.dataTransfer;
      var hatDateien = dt && Array.prototype.indexOf.call(dt.types || [], "Files") >= 0;
      if (!hatDateien) return;
      e.preventDefault();
      e.stopPropagation();
      document.body.classList.remove("zieht-datei");
      document.querySelectorAll("[data-dropzone].over").forEach(function (z) { z.classList.remove("over"); });
      dateienAusDrop(dt).then(function (liste) { hochladen(einwurf, liste); });
    }, true);

    // Auswählen (Dateien oder ganzer Ordner) und ⌘V: über denselben Weg in Paketen
    einwurf.addEventListener("change", function (e) {
      var input = e.target;
      if (!input.files || !input.files.length) return;
      if (!input.matches("[data-stapel-dateien], [data-stapel-ordner]")) return;
      e.stopPropagation();
      var liste = Array.prototype.slice.call(input.files).map(function (f) {
        return { datei: f, pfad: f.webkitRelativePath || f.name };
      });
      hochladen(einwurf, liste);
    }, true);
  }

  // ---------- Lesen: Datei für Datei, mit Fortschritt ----------

  var lesen = document.querySelector("form[data-stapel-lesen]");
  if (lesen) {
    var bar = lesen.querySelector("progress");
    var text = lesen.querySelector("[data-stapel-lesen-text]");
    var knopfL = lesen.querySelector("button");
    if (knopfL) knopfL.hidden = true;
    var schritt = function () {
      var body = new FormData();
      body.append("token", token());
      post(lesen.action, body).then(function (a) {
        if (bar) { bar.max = a.gesamt; bar.value = a.gelesen; }
        if (text) text.textContent = a.gelesen + " von " + a.gesamt + " gelesen.";
        if (a.fertig) { window.location.reload(); return; }
        schritt();
      }).catch(function () {
        if (text) text.textContent = "Beim Lesen hakt es. Drück auf „Weiter lesen“.";
        if (knopfL) knopfL.hidden = false;
      });
    };
    schritt();
  }

  // ---------- KI: Paket für Paket ----------

  var kiForm = document.querySelector("form[data-stapel-ki]");
  if (kiForm) {
    kiForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var los = function () {
        var body = new FormData();
        body.append("token", token());
        post(kiForm.action, body).then(function (a) {
          fortschritt(kiForm, a.gesendet, a.gesamt, a.gesendet + " von " + a.gesamt + " Dokumenten einsortiert …");
          if (a.fehler) { window.location.href = kiForm.action.replace(/\/ki$/, "") + "#uebersicht"; return; }
          if (a.fertig) { window.location.href = kiForm.action.replace(/\/ki$/, "") + "#uebersicht"; return; }
          los();
        }).catch(function () { kiForm.submit(); });
      };
      fortschritt(kiForm, 0, 1, "Ich schicke das erste Paket …");
      los();
    }, true);
  }

  // ---------- Übersicht: passende Felder je Ziel, alle an/aus ----------

  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest("select[data-stapel-ziel]");
    if (sel) { var li = sel.closest("li.dok"); if (li) li.setAttribute("data-ziel", sel.value); return; }
    var alle = e.target.closest && e.target.closest("[data-stapel-alle]");
    if (alle) {
      document.querySelectorAll(".doks input[type=checkbox][name^=nimm_]").forEach(function (c) { c.checked = alle.checked; });
    }
  });

  hinweiseZeigen();
})();

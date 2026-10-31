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
})();

/* KI-Werkstatt: the "Kopieren" buttons. Without JavaScript the text can still be selected by hand. */
(function () {
  "use strict";

  function selectText(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function fallbackCopy(el) {
    selectText(el);
    try { return document.execCommand("copy"); } catch (e) { return false; }
  }

  function done(button, ok) {
    var label = button.getAttribute("data-label") || button.textContent;
    button.setAttribute("data-label", label);
    button.textContent = ok ? "Kopiert" : "Markiert, jetzt Cmd+C";
    button.classList.toggle("kopiert", ok);
    setTimeout(function () { button.textContent = label; button.classList.remove("kopiert"); }, 2200);
  }

  document.addEventListener("click", function (e) {
    var button = e.target.closest && e.target.closest("[data-kopieren]");
    if (!button) return;
    var el = document.querySelector(button.getAttribute("data-kopieren"));
    if (!el) return;
    // A folded text has no innerText: open it, so copying (and the fallback selection) works
    var falte = el.closest && el.closest("details");
    if (falte && !falte.open) falte.open = true;
    var text = el.innerText || el.textContent;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(
        function () { done(button, true); },
        function () { done(button, fallbackCopy(el)); }
      );
    } else {
      done(button, fallbackCopy(el));
    }
  });
})();

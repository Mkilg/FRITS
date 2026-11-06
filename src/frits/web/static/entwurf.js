/* Draft page: sums update while Fritz types, and a fresh empty row appears as soon as the
   last one is used. The server still does the real calculation when he saves; this only
   shows what it will be. Without JavaScript the page works as before (save to see sums). */
(function () {
  "use strict";

  var table = document.querySelector("table.lines[data-live]");
  if (!table) return;
  var mode = table.getAttribute("data-mode"); // klein | regel | abroad
  var standard = Number(table.getAttribute("data-rate") || "0");
  var cols = Number(table.getAttribute("data-cols") || "5");
  var money = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Like billing.parse_amount: "40", "40,5", "1.234,50", "1.400" and "40.50".
  function parse(text) {
    var t = String(text || "").replace(/\s|€/g, "");
    if (!t) return null;
    if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
    else if (t.indexOf(",") >= 0) t = t.replace(/\./g, "").replace(",", ".");
    var n = Number(t);
    return isFinite(n) ? n : null;
  }

  function cents(n) { return Math.round((n + Math.sign(n) * 1e-9) * 100) / 100; }
  function eur(n) { return money.format(n) + " €"; }
  function rateLabel(r) { return String(r).replace(".", ","); }

  function row(label, amount, cls) {
    return '<tr' + (cls ? ' class="' + cls + '"' : "") + '><td colspan="' + cols +
      '" class="amount">' + label + '</td><td class="amount">' + eur(amount) + "</td></tr>";
  }

  function update() {
    var groups = {}, net = 0, order = [];
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var q = parse((tr.querySelector('[name="quantity"]') || {}).value);
      var p = parse((tr.querySelector('[name="price"]') || {}).value);
      var cell = tr.lastElementChild;
      if (q === null || p === null) {
        if (!tr.classList.contains("blank")) cell.textContent = "";
        return;
      }
      var line = cents(q * p);
      cell.textContent = eur(line);
      net += line;
      var sel = tr.querySelector('[name="vat"]');
      var rate = sel && sel.value !== "" ? Number(sel.value) : standard;
      if (!(rate in groups)) { groups[rate] = 0; order.push(rate); }
      groups[rate] += line;
    });
    net = cents(net);
    var html = "", vat = 0;
    if (mode === "regel") {
      order.sort(function (a, b) { return b - a; });
      if (!order.length) order = [standard], groups[standard] = 0;
      var taxes = order.map(function (r) { var v = cents(groups[r] * r / 100); vat += v; return v; });
      if (order.length > 1) {
        order.forEach(function (r) { html += row("Netto zu " + rateLabel(r) + " %", cents(groups[r])); });
        order.forEach(function (r, i) { html += row("Umsatzsteuer " + rateLabel(r) + " %", taxes[i]); });
      } else {
        html += row("Netto", net) + row("Umsatzsteuer " + rateLabel(order[0]) + " %", taxes[0]);
      }
    }
    if (mode === "abroad") html += row("Netto", net) + row("Umsatzsteuer 0 %", 0);
    html += row("Gesamt", cents(net + vat), "total");
    table.tFoot.innerHTML = html;

    // What the page said before Fritz typed may be out of date now: the question before the
    // number is given names the live total, and solved hints go away. The server checks
    // everything again when he presses the button.
    var picked = document.querySelector('select[name="customer"]');
    var option = picked && picked.selectedOptions[0];
    // The question before the fixed number shows the whole recipient block, so a missing
    // company or a cut house number is seen before it is printed for good.
    var named = option && (option.getAttribute("data-empfaenger") || option.getAttribute("data-name"));
    var firma = option && option.getAttribute("data-firma");
    var approve = document.querySelector("[data-confirm-live]");
    if (approve) {
      approve.setAttribute("data-confirm", approve.getAttribute("data-confirm-live")
        .replace("{customer}", named || approve.getAttribute("data-customer"))
        .replace("{gross}", eur(cents(net + vat)))
        .replace("{firma}", firma
          ? "\n\nAchtung: Hier steht keine Firma, die Rechnung geht an die Person selbst. Die E-Mail-Adresse gehört zu "
            + firma + ". Soll die Rechnung an die Firma gehen, trag sie vorher beim Kunden ein."
          : ""));
    }
    var hasCustomer = !!(option && option.hasAttribute("data-address"));
    var hasDates = Array.prototype.some.call(
      table.querySelectorAll('tbody [name="date"]'), function (i) { return i.value.trim() !== ""; });
    var hasLines = order.length > 0 && net !== 0;
    document.querySelectorAll("[data-gone-when]").forEach(function (el) {
      var when = el.getAttribute("data-gone-when");
      var gone = when === "dates" ? hasDates : when === "customer" ? hasCustomer : hasLines;
      el.hidden = gone;
    });
    document.querySelectorAll(".problems").forEach(function (box) {
      box.hidden = !box.querySelector("li:not([hidden])");
    });
  }

  // A new empty row as soon as the last one gets a description, so Fritz never has to
  // save just to get room for the next line.
  function growIfNeeded() {
    var blanks = table.querySelectorAll("tbody tr.blank");
    var last = blanks[blanks.length - 1];
    if (!last) return;
    var used = Array.prototype.some.call(last.querySelectorAll("input"), function (i) {
      return i.value.trim() !== "";
    });
    if (!used) return;
    var fresh = last.cloneNode(true);
    fresh.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    fresh.querySelectorAll("select").forEach(function (s) { s.selectedIndex = 0; });
    fresh.lastElementChild.textContent = "";
    last.after(fresh);
  }

  // Some hints can only be solved on another page (settings, the customer). Then the
  // question "jetzt erstellen?" would only lead to a refusal, so point at the hint instead.
  var startCustomer = customerValue();
  function customerValue() {
    var s = document.querySelector('select[name="customer"]');
    return s ? s.value : "";
  }
  document.addEventListener("click", function (e) {
    var button = e.target.closest && e.target.closest('button[value="approve"]');
    if (!button) return;
    var open = Array.prototype.filter.call(
      document.querySelectorAll(".problems li[data-elsewhere]:not([hidden])"), function (li) {
        return li.getAttribute("data-elsewhere") !== "customer" || customerValue() === startCustomer;
      });
    if (!open.length) return;
    e.preventDefault();
    var box = open[0].closest(".problems");
    box.classList.remove("nudge");
    void box.offsetWidth;
    box.classList.add("nudge");
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  }, true);

  // Lena (Runde 80): am Handy war eine Position nur durch Leeren aller Felder wegzubekommen.
  // "Entfernen" leert sie und blendet sie aus; weg ist sie, sobald Fritz speichert.
  table.querySelectorAll("[data-zeile-weg]").forEach(function (b) { b.hidden = false; });
  table.addEventListener("click", function (e) {
    var b = e.target.closest("[data-zeile-weg]");
    if (!b) return;
    var tr = b.closest("tr");
    tr.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    tr.hidden = true;
    // Kein Fokus in ein Textfeld: am Handy ginge sonst die Tastatur auf (Lena Runde 85)
    update();
  });
  table.addEventListener("input", function () { growIfNeeded(); update(); });
  table.addEventListener("change", update);
  var customerSelect = document.querySelector('select[name="customer"]');
  if (customerSelect) customerSelect.addEventListener("change", update);
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-fill]")) setTimeout(update, 0);
  });
})();

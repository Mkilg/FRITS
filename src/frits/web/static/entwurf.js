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
    var named = option && option.getAttribute("data-name");
    var approve = document.querySelector("[data-confirm-live]");
    if (approve) {
      approve.setAttribute("data-confirm", approve.getAttribute("data-confirm-live")
        .replace("{customer}", named || approve.getAttribute("data-customer"))
        .replace("{gross}", eur(cents(net + vat))));
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

  table.addEventListener("input", function () { growIfNeeded(); update(); });
  table.addEventListener("change", update);
  var customerSelect = document.querySelector('select[name="customer"]');
  if (customerSelect) customerSelect.addEventListener("change", update);
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-fill]")) setTimeout(update, 0);
  });
})();

// Das Mini-Paket im echten Browser: per Doppelklick (file://) und als Webseite (http://).
//   node minipaket/tests/e2e.cjs [ORDNER]      ORDNER: das gebaute Paket, sonst minipaket/
//   MINI_SHOTS=/pfad  speichert Bildschirmfotos in mehreren Fenstergrößen
// Prüft: keine Fehler, keine einzige Anfrage ins Netz, Kurs, Spiel, Schloss, Werkbank, Breiten.
const { chromium } = require("playwright");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const ORDNER = path.resolve(process.argv[2] || path.join(__dirname, ".."));
const SHOTS = process.env.MINI_SHOTS || "";
const SEITEN = ["index.html", "kurs.html", "werkbank.html", "prompts.html"];

function server() {
  const typen = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8" };
  const s = http.createServer((req, res) => {
    const datei = path.join(ORDNER, decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (!datei.startsWith(ORDNER) || !fs.existsSync(datei) || fs.statSync(datei).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "content-type": typen[path.extname(datei)] || "application/octet-stream" });
    fs.createReadStream(datei).pipe(res);
  });
  return new Promise((ok) => s.listen(0, "127.0.0.1", () => ok(s)));
}

async function pruefen(browser, basis, art) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const fehler = [];
  const fremd = [];
  page.on("pageerror", (e) => fehler.push(String(e)));
  page.on("console", (m) => {
    // Die Schloss-Probe soll eine CSP-Meldung auslösen, das ist der Sinn der Sache
    if (m.type() === "error" && !/Content Security Policy|example\.com|Failed to fetch/.test(m.text())) fehler.push(m.text());
  });
  page.on("request", (r) => {
    const u = r.url();
    if (!u.startsWith(basis) && !u.startsWith("data:") && !u.startsWith("blob:")) fremd.push(u);
  });
  const geladen = [];
  page.on("requestfinished", (r) => geladen.push(r.url()));

  // Jede Seite lädt ohne Fehler, alle Bilder sind da
  for (const s of SEITEN) {
    await page.goto(basis + s);
    const kaputt = await page.$$eval("img", (imgs) => imgs.filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.getAttribute("src")));
    assert.deepEqual(kaputt, [], `${art} ${s}: Bilder fehlen`);
  }

  // ---- Kurs ----
  await page.goto(basis + "kurs.html");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  assert.ok(await page.isVisible("#karte"), "Karte zuerst");
  assert.ok(!(await page.isVisible("#start-hallo")), "nur ein Teil sichtbar");
  await page.click('[data-halt="start"] a');
  await page.waitForSelector("#start-hallo", { state: "visible" });
  await page.click("#start-hallo .weiter-reihe .primary");
  await page.waitForSelector("#start-brauchst", { state: "visible" });

  // Schwärz-Spiel
  await page.goto(basis + "kurs.html#sensibel-spiel");
  await page.click("#sensibel-spiel [data-pruefen]");
  assert.match(await page.textContent("#sensibel-spiel [data-ergebnis]"), /0 von 9/);
  for (const w of await page.$$("#sensibel-spiel [data-geheim]")) await w.click();
  await page.click("#sensibel-spiel [data-pruefen]");
  assert.match(await page.textContent("#sensibel-spiel [data-ergebnis]"), /Perfekt/);
  assert.match(await page.textContent("#sensibel-spiel .uebung-marke"), /gelöst/);

  // Hin und zurück
  await page.goto(basis + "kurs.html#schwaerzen-probieren");
  await page.click("[data-probe-schwaerzen]");
  const gesehen = await page.textContent("[data-probe-ergebnis]");
  assert.ok(!/Jonas|Okafor|DE89/.test(gesehen), gesehen);
  await page.click("[data-probe-zurueck]");
  assert.match(await page.textContent("[data-probe-fertig]"), /Liebe Miriam Okafor,[\s\S]*Grüße\nJonas$/);
  assert.match(await page.textContent("#schwaerzen-probieren .uebung-marke"), /gelöst/);

  // Das Schloss der Seite
  await page.goto(basis + "kurs.html#schloss-bitte");
  await page.click("[data-schloss-test]");
  await page.waitForFunction(() => /⛔|😬/.test(document.querySelector("[data-schloss-ergebnis]").textContent));
  assert.match(await page.textContent("[data-schloss-ergebnis]"), /Blockiert, noch bevor/);
  assert.ok(!geladen.some((u) => u.includes("example.com")), "Die Probe darf nie rausgehen");

  // Schalter, Reihenfolge, Spickzettel bis zur Urkunde
  await page.goto(basis + "kurs.html#schloss-schalter");
  for (const s of await page.$$("#schloss-schalter [data-schalter]")) await s.click();
  assert.match(await page.textContent("#schloss-schalter .uebung-marke"), /gelöst/);
  await page.goto(basis + "kurs.html#spickzettel-rezept");
  for (const pos of [1, 2, 3, 4, 5]) await page.click(`#spickzettel-rezept [data-pos="${pos}"]`);
  assert.match(await page.textContent("[data-reihen-meldung]"), /Genau so/);
  await page.goto(basis + "kurs.html#spickzettel-fragen");
  for (const h of await page.$$("#spickzettel-fragen input[type=checkbox]")) await h.check();
  await page.click("#spickzettel-fragen [data-spick-fertig] a");
  await page.waitForSelector("#urkunde", { state: "visible" });
  assert.match(await page.textContent("#urkunde h2"), /Fast geschafft/); // nicht alles gesehen

  // Alles durchklicken: dann gibt es den Stempel
  await page.goto(basis + "kurs.html#start-hallo");
  const quizze = [];
  for (let i = 0; i < 40; i += 1) {
    const id = await page.evaluate(() => document.querySelector(".kurs-teil.zu-sehen").id);
    if (await page.$(`#${id} [data-quiz]`)) quizze.push(id);
    for (const f of await page.$$(`#${id} [data-frage]`)) await (await f.$("[data-richtig]")).click();
    const weiter = await page.$(`#${id} .weiter-reihe .primary`);
    if (!weiter) break;
    const ziel = (await weiter.getAttribute("href")).slice(1);
    await weiter.click();
    await page.waitForSelector("#" + ziel, { state: "visible" });
  }
  for (const w of await page.$$("#sensibel-spiel [data-geheim]")) await page.evaluate((el) => el.setAttribute("aria-pressed", "true"), w);
  await page.goto(basis + "kurs.html#urkunde");
  assert.match(await page.textContent("#urkunde h2"), /^Geschafft!$/);
  await page.goto(basis + "kurs.html#karte");
  assert.equal(await page.$$eval(".halt.geschafft", (l) => l.length), 6);

  // ---- Werkbank ----
  await page.goto(basis + "werkbank.html");
  await page.click("[data-beispiel]");
  await page.waitForFunction(() => document.querySelector("[data-prompt]").textContent.includes("[PERSON_1]"));
  let prompt = await page.textContent("[data-prompt]");
  for (const geheim of ["Albrecht", "Gartenweg", "50667", "DE89", "koenig@", "9988776", "Petra", "NK-2025"]) {
    assert.ok(!prompt.includes(geheim), geheim);
  }
  assert.ok(prompt.includes("Hausverwaltung König"), "der Rest-Nachname wird erst als Hinweis gezeigt");
  await page.click("[data-hinweise] button.primary");
  prompt = await page.textContent("[data-prompt]");
  assert.ok(!prompt.includes("König"));
  // Ein Wort von Hand schwärzen und wieder aufdecken
  await page.click('[data-ergebnis] .w:text-is("Nebenkostenabrechnung")');
  assert.ok(!(await page.textContent("[data-prompt]")).includes("Nebenkostenabrechnung"));
  assert.equal(await page.inputValue("[data-woerter]"), "Nebenkostenabrechnung");
  await page.click('[data-ergebnis] .balken[data-schluessel="GEHEIM:nebenkostenabrechnung"]');
  assert.ok((await page.textContent("[data-prompt]")).includes("Nebenkostenabrechnung"));
  // Aufgabe und Antwort
  await page.fill("[data-aufgabe]", "Ist die Nachzahlung plausibel?");
  assert.ok((await page.textContent("[data-prompt]")).includes("Aufgabe: Ist die Nachzahlung plausibel?"));
  await page.fill("[data-antwort]", "Sehr geehrte Frau [PERSON_1], ich überweise auf [IBAN_1]. Gruß an [PERSON_2]");
  assert.equal(await page.textContent("[data-zurueck]"),
    "Sehr geehrte Frau Albrecht, ich überweise auf DE89 3704 0044 0532 0130 00. Gruß an Petra König");
  // Eine Textdatei hineinziehen bzw. auswählen
  await page.setInputFiles("[data-datei]", { name: "brief.txt", mimeType: "text/plain", buffer: Buffer.from("Hallo Herr Yilmaz,\nTel. 0171 2345678") });
  await page.waitForFunction(() => document.querySelector("[data-nur-text]").textContent.includes("[TEL_1]"));
  assert.equal(await page.textContent("[data-nur-text]"), "Hallo Herr [PERSON_1],\nTel. [TEL_1]");
  await page.setInputFiles("[data-datei]", { name: "scan.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF") });
  assert.match(await page.textContent("[data-datei-meldung]"), /kann die Werkbank nicht lesen/);

  // ---- Prompts ----
  await page.goto(basis + "prompts.html");
  assert.ok((await page.$$(".kopierbox")).length >= 9);

  // ---- Breiten: nichts ragt über den Rand ----
  for (const breite of [1680, 1280, 1024, 820, 390]) {
    await page.setViewportSize({ width: breite, height: breite === 390 ? 844 : 900 });
    for (const s of SEITEN.concat(["kurs.html#sensibel-treppe", "kurs.html#schloss-ordner", "kurs.html#sensibel-spiel", "kurs.html#schwaerzen-probieren", "kurs.html#schwaerzen-weg"])) {
      await page.goto(basis + s);
      if (s.endsWith("probieren")) { await page.click("[data-probe-schwaerzen]"); await page.click("[data-probe-zurueck]"); }
      if (s === "werkbank.html") await page.click("[data-beispiel]");
      const zuBreit = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(zuBreit <= 0, `${art} ${s} bei ${breite}px: ${zuBreit}px zu breit`);
      if (SHOTS && art === "file") {
        fs.mkdirSync(SHOTS, { recursive: true });
        await page.screenshot({ path: path.join(SHOTS, `${s.replace(/[#.]/g, "-")}-${breite}.png`), fullPage: breite === 390 || breite === 1280 });
      }
    }
  }

  assert.deepEqual(fremd, [], `${art}: Anfragen ins Netz`);
  assert.deepEqual(fehler, [], `${art}: Fehler`);
  await context.close();
  console.log(`✓ ${art}: alles gut`);
}

(async () => {
  const browser = await chromium.launch();
  try {
    await pruefen(browser, "file://" + ORDNER + "/", "file");
    const s = await server();
    try {
      await pruefen(browser, `http://127.0.0.1:${s.address().port}/`, "http");
    } finally {
      s.close();
    }
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

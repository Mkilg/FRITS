// End-to-end check of the browser version in a real Chromium (Playwright).
//
//   uv run python browser/build.py && node browser/tests/e2e.cjs
//
// It serves dist/ under /FRITS/ like GitHub Pages, starts with an empty browser profile and
// walks through: start, setup with a (wrong) Claude key, reading an old invoice from a photo,
// customer, invoice with PDF and e-invoice, reload (data still there), backup and restore,
// window sizes. The 180 MB redaction model can't be downloaded in tests, so a small word list
// stands in for it (the same answer format). Screenshots go to $FRITS_E2E_SHOTS if set.
"use strict";
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");

const HERE = __dirname;
const BROWSER = path.dirname(HERE);
const PORT = Number(process.env.FRITS_E2E_PORT || 8811);
const BASE = `http://localhost:${PORT}/FRITS/`;
const SHOTS = process.env.FRITS_E2E_SHOTS;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "frits-e2e-"));
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

function overlay() {
  // The real helper worker, with the model call replaced by a word list.
  const dir = path.join(tmp, "overlay");
  fs.mkdirSync(dir);
  const real = fs.readFileSync(path.join(BROWSER, "dist", "helper-worker.js"), "utf8");
  const marker = "async function findeNamen(text) {";
  assert.ok(real.includes(marker), "helper-worker.js changed: update the test double");
  const fake = `const TEST_NAMEN = { Berger: "PER", Martina: "PER", Fritz: "PER", Beispiel: "PER", Hamburg: "LOC", Lüneburg: "LOC" };
async function findeNamen(text) {
  const out = [];
  for (const [w, label] of Object.entries(TEST_NAMEN)) {
    for (let i = text.indexOf(w); i >= 0; i = text.indexOf(w, i + 1)) out.push({ label, start: i, end: i + w.length });
  }
  return out;
}
async function findeNamenEcht(text) {`;
  fs.writeFileSync(path.join(dir, "helper-worker.js"), real.replace(marker, fake));
  return dir;
}

function startServer() {
  const server = spawn("python3", [path.join(BROWSER, "serve.py"), "--port", String(PORT), "--prefix", "/FRITS", "--overlay", overlay()], { stdio: "ignore" });
  return new Promise((resolve) => setTimeout(() => resolve(server), 800));
}

async function shot(page, name) {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name + ".png") });
}

async function openFrits(profile, viewport = { width: 1440, height: 900 }, hash = "") {
  const ctx = await chromium.launchPersistentContext(profile, { viewport, acceptDownloads: true });
  const page = ctx.pages()[0] || (await ctx.newPage());
  page.on("dialog", (d) => d.accept());
  if (process.env.FRITS_E2E_DEBUG) {
    page.on("console", (m) => console.log("   [page]", m.text().slice(0, 200)));
    page.on("worker", (w) => w.on("console", (m) => console.log("   [worker]", m.text().slice(0, 200))));
  }
  const t0 = Date.now();
  await page.goto(BASE + hash);
  await page.waitForSelector("#app:not([hidden])", { timeout: 180000 });
  const startMs = Date.now() - t0;
  const frame = () => page.frames().find((f) => f.url().includes("/FRITS/app"));
  for (let i = 0; i < 100 && !frame(); i++) await page.waitForTimeout(100);
  await frame().waitForLoadState();
  const f = () => frame();
  const nav = async (action) => {
    await Promise.all([f().waitForNavigation({ timeout: 120000 }), action()]);
  };
  const go = (p) => nav(() => page.evaluate((u) => { document.getElementById("app").src = u; }, "/FRITS/app" + p));
  return { ctx, page, f, nav, go, startMs };
}

const steps = [];
async function step(name, fn) {
  const t = Date.now();
  await fn();
  steps.push(`ok  ${name} (${((Date.now() - t) / 1000).toFixed(1)} s)`);
  console.log(steps[steps.length - 1]);
}

(async () => {
  const server = await startServer();
  const profile = path.join(tmp, "profil");
  let failed = false;
  try {
    let s = await openFrits(profile, undefined, "#/setup/welcome");
    console.log(`FRITS started in ${(s.startMs / 1000).toFixed(1)} s (first visit)`);
    const { page, f, nav, go } = s;
    const click = (sel) => nav(() => f().click(sel));

    await step("Einrichtung: KI wählen, Schlüssel testen", async () => {
      await click("button[value=next]");
      await f().check("input[value=claude]");
      await click("button[value=next]");
      await f().fill("input[name=api_key]", "sk-ant-api03-absichtlich-falsch");
      await click("button[value=test]");
      const text = await f().locator("main").innerText();
      assert.match(text, /nur in diesem Browser auf diesem Gerät/);
      assert.doesNotMatch(text, /Schlüsselbund/);
      // Either Claude answered "unknown key" (internet) or there was no connection (offline CI).
      assert.match(text, /Claude kennt diesen Schlüssel nicht|Keine Verbindung zu Claude/);
      console.log("    Claude:", /kennt diesen Schlüssel nicht/.test(text) ? "hat geantwortet (401)" : "nicht erreichbar");
      await shot(page, "01-schluessel");
    });

    await step("Foto einer alten Rechnung lesen (Tesseract im Browser)", async () => {
      await click("button[value=next]");
      await f().setInputFiles("input[name=files]", path.join(HERE, "alte-rechnung.png"));
      await page.waitForFunction(() => {
        const doc = document.getElementById("app").contentDocument;
        return doc && doc.querySelector("[name=email]") && doc.querySelector("[name=email]").value;
      }, null, { timeout: 120000 }).catch(async (e) => {
        console.log((await f().locator("body").innerText()).slice(-1200));
        throw e;
      });
      const v = await f().evaluate(() => Object.fromEntries(["email", "iban", "phone"].map((n) => [n, document.querySelector(`[name=${n}]`).value])));
      assert.equal(v.email, "fritz@example.de");
      assert.equal(v.iban.replace(/\s/g, ""), "DE89370400440532013000");
      await shot(page, "02-foto-gelesen");
    });

    await step("Einrichtung abschließen, Schwärzen-Test", async () => {
      await f().fill("input[name=name]", "Fritz Beispiel");
      await f().fill("textarea[name=address]", "Musterweg 1\n20095 Hamburg");
      await f().fill("input[name=hourly_rate]", "60");
      await click("button[value=next]");
      await f().check("input[value=kleinunternehmer]");
      await click("button[value=next]");
      await click("button[value=redact]");
      const ki = await f().locator("main").innerText();
      assert.match(ki, /\[IBAN_1\]/);
      assert.match(ki, /\[EMAIL_1\]/);
      await shot(page, "03-schwaerzen");
      await click("button[value=skip_learning]");
      await click("button[value=next]");
      await click("button[value=next]");
      assert.ok(f().url().endsWith("/FRITS/app/"), f().url());
    });

    await step("Kunde und Rechnung mit PDF", async () => {
      await go("/customers/new");
      await f().fill("input[name=name]", "Martina Berger");
      await f().fill("textarea[name=address]", "Lindenallee 7\n21335 Lüneburg");
      await nav(() => f().click('button:has-text("Speichern")'));
      await go("/drafts");
      await nav(() => f().click('button:has-text("Neue Rechnung von Hand")'));
      await f().selectOption("select[name=customer]", { label: "Martina Berger" });
      await f().fill("input[name=description]", "Hecke geschnitten");
      await f().fill("input[name=quantity]", "3");
      await f().fill("input[name=price]", "60");
      await click("button[value=approve]");
      await go("/invoices");
      await shot(page, "04-rechnungen");
      const href = await f().locator('a[href$="/pdf"]').first().getAttribute("href");
      assert.ok(href.startsWith("/FRITS/app/"), href);
      const pdf = await f().evaluate(async (u) => {
        const r = await fetch(u);
        const b = new Uint8Array(await r.arrayBuffer());
        let s = ""; for (const x of b) s += String.fromCharCode(x);
        return { status: r.status, type: r.headers.get("content-type"), head: s.slice(0, 5), xml: s.includes("/EmbeddedFiles") && s.includes("/AFRelationship"), b64: btoa(s) };
      }, href);
      assert.equal(pdf.status, 200);
      assert.equal(pdf.head, "%PDF-");
      assert.ok(pdf.xml, "e-invoice XML is embedded in the PDF");
      if (SHOTS) fs.writeFileSync(path.join(SHOTS, "rechnung.pdf"), Buffer.from(pdf.b64, "base64"));
    });

    let backup;
    await step("Sicherung herunterladen", async () => {
      const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#sichern")]);
      backup = path.join(tmp, "sicherung.zip");
      await dl.saveAs(backup);
      assert.ok(fs.statSync(backup).size > 1000);
    });
    await s.ctx.close();

    s = await openFrits(profile, undefined, "#/invoices");
    console.log(`FRITS started in ${(s.startMs / 1000).toFixed(1)} s (second visit)`);
    await step("Nach dem Neustart ist alles noch da", async () => {
      const text = await s.f().locator("main").innerText();
      assert.match(text, /Martina Berger/);
      assert.match(text, /180,00/);
    });
    await s.ctx.close();

    await step("Ohne Internet starten", async () => {
      const ctx = await chromium.launchPersistentContext(profile, { viewport: { width: 1280, height: 800 } });
      const page = ctx.pages()[0] || (await ctx.newPage());
      await page.goto(BASE + "#/invoices"); // online once, so the service worker is in charge
      await page.waitForSelector("#app:not([hidden])", { timeout: 180000 });
      await ctx.setOffline(true);
      await page.reload();
      await page.waitForSelector("#app:not([hidden])", { timeout: 180000 });
      const frame = page.frames().find((f) => f.url().includes("/FRITS/app"));
      await frame.waitForLoadState();
      assert.match(await frame.locator("main").innerText(), /Martina Berger/);
      await ctx.close();
    });

    // Restore into a brand-new browser profile: like moving to another computer.
    s = await openFrits(path.join(tmp, "neu"), undefined, "#/invoices");
    await step("Sicherung in einem leeren Browser einspielen", async () => {
      assert.doesNotMatch(await s.f().locator("main").innerText(), /Martina Berger/);
      const done = s.page.waitForEvent("download"); // the safety copy of the current state
      await s.page.setInputFiles("#einspielen", backup);
      await done;
      await s.page.waitForSelector("#fehler[open]");
      await s.page.click("#fehler button");
      await s.go("/invoices");
      assert.match(await s.f().locator("main").innerText(), /Martina Berger/);
    });
    await s.ctx.close();

    for (const width of [1680, 1280, 1024, 820, 390]) {
      await step(`Fenster ${width} px: keine Querlage, Leiste passt`, async () => {
        const v = await openFrits(profile, { width, height: width < 500 ? 844 : 900 });
        const m = await v.page.evaluate(() => ({
          scroll: document.documentElement.scrollWidth - innerWidth,
          bar: document.getElementById("leiste").getBoundingClientRect().height,
          frame: document.getElementById("app").getBoundingClientRect().height,
          inner: innerHeight,
        }));
        const frameScroll = await v.f().evaluate(() => document.documentElement.scrollWidth - innerWidth);
        assert.ok(m.scroll <= 0, `page scrolls sideways by ${m.scroll}px`);
        assert.ok(frameScroll <= 0, `app scrolls sideways by ${frameScroll}px`);
        assert.ok(m.bar < (width < 700 ? 140 : 60), `bar is ${m.bar}px high`);
        assert.ok(m.frame > m.inner * 0.75, "the app keeps most of the window");
        await shot(v.page, `05-fenster-${width}`);
        await v.ctx.close();
      });
    }
  } catch (err) {
    failed = true;
    console.error("FEHLER:", err);
  } finally {
    server.kill();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  console.log(failed ? "\nE2E FAILED" : `\nE2E passed: ${steps.length} steps`);
  process.exit(failed ? 1 : 0);
})();

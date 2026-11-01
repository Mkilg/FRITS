// FRITS im Browser: the page around the app. It starts Python (Pyodide) in a worker, shows the
// app in a frame and passes the frame's requests (via the service worker) to FRITS.
const BASE = new URL("./", location.href).href;
const APP_PATH = new URL("app", BASE).pathname; // e.g. "/FRITS/app"
const $ = (id) => document.getElementById(id);

function fortschritt(text, anteil) {
  $("laden-text").textContent = text;
  if (anteil != null) $("laden-balken").style.width = Math.max(3, Math.round(anteil * 100)) + "%";
}

function stopp(titel, text) {
  $("laden").classList.add("fehler");
  $("laden").querySelector("h1").textContent = titel;
  fortschritt(text);
  $("laden").querySelector(".balken").hidden = true;
}

function meldung(titel, text) {
  $("fehler-titel").textContent = titel;
  $("fehler-text").textContent = text;
  $("fehler").showModal();
}

async function serviceWorkerBereit() {
  if (!("serviceWorker" in navigator)) {
    stopp("Dieser Browser kann FRITS nicht starten",
      "Bitte nimm einen aktuellen Safari, Chrome, Edge oder Firefox. In einem privaten Fenster geht es oft auch nicht.");
    return false;
  }
  await navigator.serviceWorker.register("sw.js");
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller || !self.crossOriginIsolated) {
    // First visit: the service worker is there now, but this page was loaded before it.
    const versuche = Number(sessionStorage.getItem("frits-neu") || "0");
    if (versuche < 2) {
      sessionStorage.setItem("frits-neu", String(versuche + 1));
      location.reload();
      return false;
    }
    stopp("FRITS kann hier nicht starten",
      "Der Browser erlaubt die nötige Abschirmung nicht (cross-origin isolation). Bitte nimm einen aktuellen Browser und kein privates Fenster.");
    return false;
  }
  sessionStorage.removeItem("frits-neu");
  return true;
}

function einTabSperre() {
  // Two tabs would each have their own copy of the data and overwrite each other.
  return new Promise((resolve) => {
    if (!navigator.locks) return resolve(true);
    navigator.locks.request("frits-tab", { ifAvailable: true }, (lock) => {
      if (!lock) { resolve(false); return; }
      resolve(true);
      return new Promise(() => {}); // hold the lock while this tab is open
    });
  });
}

let pyWorker, helperWorker;
let naechsteId = 1;
const offen = new Map();

function anPython(message, transfer = []) {
  const id = naechsteId++;
  return new Promise((resolve, reject) => {
    offen.set(id, { resolve, reject });
    pyWorker.postMessage({ ...message, id }, transfer);
  });
}

function startWorker(manifest) {
  const ctl = new Int32Array(new SharedArrayBuffer(8));
  const data = new Uint8Array(new SharedArrayBuffer(8 * 1024 * 1024));
  const kanal = new MessageChannel();

  helperWorker = new Worker("helper-worker.js", { type: "module" });
  helperWorker.postMessage({ type: "start", base: BASE, ctl, data, port: kanal.port2 }, [kanal.port2]);
  helperWorker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "modell") modellStatus(m);
  };

  pyWorker = new Worker("py-worker.js", { type: "module" });
  pyWorker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "fortschritt") return fortschritt(m.text, m.anteil);
    if (m.type === "gespeichert") return speicherStatus(m.ok);
    const warten = offen.get(m.id);
    if (!warten) return;
    offen.delete(m.id);
    m.error ? warten.reject(new Error(m.error)) : warten.resolve(m);
  };
  pyWorker.onerror = (e) => stopp("FRITS ist abgestürzt", String(e.message || e));
  return anPython(
    { type: "start", base: BASE, prefix: APP_PATH, manifest, ctl, data, port: kanal.port1 },
    [kanal.port1],
  );
}

function speicherStatus(ok) {
  const el = $("speicher-status");
  el.textContent = ok ? "gespeichert" : "Speichern fehlgeschlagen!";
  el.classList.toggle("warnung", !ok);
}

function modellStatus(m) {
  const el = $("modell-status");
  if (m.status === "laedt") el.textContent = `Schwärz-Modell wird geladen: ${Math.round(m.anteil * 100)} %`;
  else if (m.status === "bereit") el.textContent = "";
  else if (m.status === "fehler") el.innerHTML = '<span class="warnung">Schwärz-Modell fehlt</span> (KI aus, alles andere geht)';
}

// The service worker hands us the app frame's requests.
navigator.serviceWorker?.addEventListener("message", async (event) => {
  const m = event.data || {};
  const port = event.ports[0];
  if (m.type === "bist-du-frits") { port.postMessage({ ja: Boolean(pyWorker) }); return; }
  if (m.type !== "anfrage") return;
  try {
    const answer = await anPython(
      { type: "anfrage", method: m.method, url: m.url, headers: m.headers, body: m.body },
      m.body ? [m.body] : [],
    );
    port.postMessage({ status: answer.status, headers: answer.headers, body: answer.body }, [answer.body]);
  } catch (err) {
    port.postMessage({ error: String(err.message || err) });
  }
});

function rahmenPfad() {
  // The app's address shows after "#", so reloading the page returns to the same screen.
  const pfad = location.hash.startsWith("#/") ? location.hash.slice(1) : "/";
  return APP_PATH + pfad;
}

function zeigeApp() {
  const frame = $("app");
  frame.src = rahmenPfad();
  frame.addEventListener("load", () => {
    try {
      const doc = frame.contentDocument;
      const loc = frame.contentWindow.location;
      if (loc.pathname.startsWith(APP_PATH)) {
        const pfad = loc.pathname.slice(APP_PATH.length) || "/";
        history.replaceState(null, "", "#" + pfad + loc.search);
      }
      document.title = doc.title ? `${doc.title} · im Browser` : "FRITS im Browser";
    } catch { /* not our page */ }
  });
  $("laden").hidden = true;
  frame.hidden = false;
  $("leiste").hidden = false;
}

function herunterladen(bytes, name) {
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function heute() {
  return new Date().toISOString().slice(0, 10);
}

async function sichern(name) {
  const answer = await anPython({ type: "sicherung" });
  herunterladen(answer.body, name || `FRITS-Sicherung-${heute()}.zip`);
  try { localStorage.setItem("frits-letzte-sicherung", heute()); } catch { /* private mode */ }
}

async function einspielen(file) {
  const ok = confirm(
    `„${file.name}“ einspielen?\n\nDas ersetzt alles, was jetzt in FRITS in diesem Browser ist. ` +
    "Zur Sicherheit lädt FRITS deinen jetzigen Stand vorher als Sicherung herunter.");
  if (!ok) return;
  await sichern(`FRITS-vor-dem-Einspielen-${heute()}.zip`);
  const bytes = await file.arrayBuffer();
  const answer = await anPython({ type: "einspielen", body: bytes }, [bytes]);
  meldung("Sicherung eingespielt", `${answer.count} Dateien sind wieder da.`);
  $("app").src = APP_PATH + "/";
}

function knoepfe() {
  $("sichern").addEventListener("click", () => sichern().catch((e) => meldung("Sicherung fehlgeschlagen", e.message)));
  $("einspielen").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) einspielen(file).catch((err) => meldung("Einspielen fehlgeschlagen", err.message));
  });
  $("info-knopf").addEventListener("click", () => $("info").showModal());
  // Write everything to the browser's storage before the tab goes away.
  addEventListener("pagehide", () => pyWorker?.postMessage({ type: "speichern" }));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") pyWorker?.postMessage({ type: "speichern" });
  });
}

async function main() {
  if (!(await serviceWorkerBereit())) return;
  if (!(await einTabSperre())) {
    stopp("FRITS ist schon offen",
      "FRITS läuft schon in einem anderen Tab oder Fenster. Bitte arbeite dort weiter (zwei Tabs würden sich gegenseitig überschreiben).");
    return;
  }
  navigator.storage?.persist?.().catch(() => {});
  fortschritt("Lade die Liste der Programmteile …", 0.02);
  const manifest = await (await fetch("manifest.json", { cache: "no-cache" })).json();
  let info;
  try {
    info = await startWorker(manifest);
  } catch (err) {
    stopp("FRITS konnte nicht starten", String(err.message || err));
    return;
  }
  navigator.serviceWorker.controller.postMessage({ type: "ich-bin-frits" });
  knoepfe();
  zeigeApp();
  // With an AI key set up, load the redaction model in the background (about 180 MB, once),
  // so the first AI step doesn't wait for it. Without a key nobody needs it yet.
  if (info.hatSchluessel) setTimeout(() => helperWorker.postMessage({ type: "modell-vorladen" }), 4000);
}

main().catch((err) => stopp("FRITS konnte nicht starten", String(err.message || err)));

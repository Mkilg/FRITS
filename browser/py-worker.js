// FRITS im Browser: Python (Pyodide) with the real FRITS app, in its own worker.
// The files live in the browser's IndexedDB (mounted at /frits); the API key in a separate store.
import { loadPyodide } from "./pyodide/pyodide.mjs";

let py, runtime, ctl, data, helperPort;
let queue = Promise.resolve();
let speichernTimer = null;
const decoder = new TextDecoder();

function melde(text, anteil) {
  self.postMessage({ type: "fortschritt", text, anteil });
}

// --- blocking calls into the helper worker (redaction model, text recognition) ---------------
// Python code is synchronous, the model is not. So we wait here with Atomics.wait until the helper
// writes its answer into shared memory. This only works in a worker and needs cross-origin
// isolation, which the service worker provides.
function blockierend(kind, payload, transfer = []) {
  Atomics.store(ctl, 0, 0);
  helperPort.postMessage({ kind, payload }, transfer);
  const ergebnis = Atomics.wait(ctl, 0, 0, 15 * 60 * 1000);
  if (ergebnis === "timed-out") return JSON.stringify({ error: "Zeitüberschreitung" });
  const len = Atomics.load(ctl, 1);
  const copy = new Uint8Array(len);
  copy.set(data.subarray(0, len));
  return decoder.decode(copy);
}

const services = {
  ner(text) { return blockierend("ner", String(text)); },
  ocr(bytes, lang) {
    const buf = bytes.slice().buffer; // a copy we may hand over
    return blockierend("ocr", { bytes: buf, lang: String(lang) }, [buf]);
  },
};

function syncfs(populate) {
  return new Promise((resolve, reject) => py.FS.syncfs(populate, (err) => (err ? reject(err) : resolve())));
}

function speichernBald() {
  clearTimeout(speichernTimer);
  speichernTimer = setTimeout(speichern, 250);
}

async function speichern() {
  clearTimeout(speichernTimer);
  try {
    await syncfs(false);
    self.postMessage({ type: "gespeichert", ok: true });
  } catch (err) {
    self.postMessage({ type: "gespeichert", ok: false, error: String(err) });
  }
}

async function start({ base, prefix, manifest, ctl: c, data: d, port }) {
  ctl = c;
  data = d;
  helperPort = port;
  melde("Lade Python …", 0.05);
  py = await loadPyodide({ indexURL: base + "pyodide/" });
  melde("Lade Programmteile …", 0.3);
  await py.loadPackage(manifest.pyodidePackages, { messageCallback: () => {} });
  melde("Installiere FRITS …", 0.6);
  const micropip = py.pyimport("micropip");
  const wheels = manifest.wheels.map((name) => `${base}wheels/${name}?v=${manifest.version}`);
  await micropip.install.callKwargs(wheels, { deps: false, reinstall: true });

  melde("Öffne deine Daten in diesem Browser …", 0.8);
  for (const dir of ["/frits", "/frits-geheim"]) {
    py.FS.mkdirTree(dir);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, dir);
  }
  await syncfs(true);

  melde("Starte FRITS …", 0.9);
  py.globals.set("frits_services", services);
  py.globals.set("frits_prefix", prefix);
  await py.runPythonAsync(`
from frits.browser import runtime
runtime.install_shims(frits_services)
runtime.start(prefix=frits_prefix)
`);
  runtime = py.pyimport("frits.browser.runtime");
  await speichern();
  return { hatSchluessel: Boolean(runtime.has_any_key()) };
}

async function anfrage({ method, url, headers, body }) {
  const answer = await runtime.handle(method, url, headers, body ? new Uint8Array(body) : null);
  const result = answer.toJs({ dict_converter: Object.fromEntries });
  answer.destroy();
  const bytes = result.body; // Uint8Array
  speichernBald();
  return { status: result.status, headers: result.headers, body: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
}

async function sicherung() {
  await speichern();
  const proxy = runtime.backup_zip();
  const bytes = proxy.toJs();
  proxy.destroy();
  return { body: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
}

async function einspielen({ body }) {
  const count = runtime.restore_zip(new Uint8Array(body));
  await speichern();
  return { count };
}

const handlers = { start, anfrage, sicherung, einspielen, speichern };

self.onmessage = (event) => {
  const m = event.data;
  const handler = handlers[m.type];
  if (!handler) return;
  // One request at a time: FRITS was written for one person clicking, not for parallel calls.
  queue = queue.then(async () => {
    try {
      const result = (await handler(m)) || {};
      const transfer = result.body instanceof ArrayBuffer ? [result.body] : [];
      if (m.id) self.postMessage({ id: m.id, ...result }, transfer);
    } catch (err) {
      if (m.id) self.postMessage({ id: m.id, error: String(err.message || err) });
    }
  });
};

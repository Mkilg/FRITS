// FRITS im Browser: the helper worker. It does the two jobs Python can't do in the browser:
//  - find names, places and firms (the NER layer of the redaction), with a German-capable model
//  - read text from photos (Tesseract, the same engine as on the Mac, compiled to WebAssembly)
// The Python worker waits for the answer (Atomics), so every answer goes into shared memory.

export const MODELL = "Xenova/bert-base-multilingual-cased-ner-hrl"; // PER, ORG, LOC in 10 languages incl. German
const CHUNK = 800; // characters per model call; the model reads at most 512 word pieces

let base, ctl, data, ner = null, nerLaden = null, tesseract = null;
const encoder = new TextEncoder();

function antworte(objekt) {
  let bytes = encoder.encode(JSON.stringify(objekt));
  if (bytes.length > data.length) bytes = encoder.encode(JSON.stringify({ error: "Antwort zu groß" }));
  data.set(bytes);
  Atomics.store(ctl, 1, bytes.length);
  Atomics.store(ctl, 0, 1);
  Atomics.notify(ctl, 0);
}

function modellMeldung(status, anteil = 0) {
  self.postMessage({ type: "modell", status, anteil });
}

async function ladeModell() {
  if (ner) return ner;
  if (!nerLaden) {
    nerLaden = (async () => {
      const { pipeline, env } = await import("./vendor/transformers/transformers.web.min.js");
      env.allowLocalModels = false;
      env.useBrowserCache = true; // downloaded once, then kept by the browser
      env.backends.onnx.wasm.wasmPaths = {
        mjs: base + "vendor/ort/ort-wasm-simd-threaded.jsep.mjs",
        wasm: base + "vendor/ort/ort-wasm-simd-threaded.jsep.wasm",
      };
      const geladen = {};
      const p = await pipeline("token-classification", MODELL, {
        dtype: "q8",
        device: "wasm",
        progress_callback: (info) => {
          if (info.status === "progress" && info.total) {
            geladen[info.file] = [info.loaded, info.total];
            const [l, t] = Object.values(geladen).reduce((a, b) => [a[0] + b[0], a[1] + b[1]], [0, 0]);
            modellMeldung("laedt", t ? l / t : 0);
          }
        },
      });
      modellMeldung("bereit", 1);
      ner = p;
      return p;
    })().catch((err) => {
      nerLaden = null;
      modellMeldung("fehler");
      throw err;
    });
  }
  return nerLaden;
}

// Split at line ends (lines are separate facts in FRITS notes), long lines at spaces.
export function stuecke(text) {
  const teile = [];
  let start = 0;
  while (start < text.length) {
    let ende = Math.min(text.length, start + CHUNK);
    if (ende < text.length) {
      const zeile = text.lastIndexOf("\n", ende);
      const leer = text.lastIndexOf(" ", ende);
      if (zeile > start) ende = zeile + 1;
      else if (leer > start) ende = leer + 1;
    }
    teile.push([start, text.slice(start, ende)]);
    start = ende;
  }
  return teile;
}

function tag(entity) {
  const p = entity[0];
  return entity[1] === "-" && "BIES".includes(p) ? [p, entity.slice(2)] : ["I", entity];
}

const WORT = /[\p{L}\p{N}-]/u;

// Model tokens (word pieces with labels, no positions) -> entities with character positions.
export function entitaeten(text, tokens, offset = 0) {
  const spans = [];
  let cursor = 0;
  for (const tok of tokens) {
    const stueck = tok.word.startsWith("##") ? tok.word.slice(2) : tok.word;
    if (!stueck) continue;
    const pos = text.indexOf(stueck, cursor);
    if (pos < 0) continue;
    cursor = pos + stueck.length;
    if (tok.entity === "O") continue;
    const [p, label] = tag(tok.entity);
    const letzte = spans[spans.length - 1];
    const weiter = letzte && letzte.label === label && letzte.index === tok.index - 1
      && (p !== "B" || tok.word.startsWith("##"));
    if (weiter) {
      letzte.end = cursor;
      letzte.index = tok.index;
    } else {
      spans.push({ label, start: pos, end: cursor, index: tok.index });
    }
  }
  // A name never ends in the middle of a word: "Berg|ers" becomes "Bergers".
  return spans.map((s) => {
    let { start, end } = s;
    while (start > 0 && WORT.test(text[start - 1])) start--;
    while (end < text.length && WORT.test(text[end])) end++;
    return { label: s.label, start: start + offset, end: end + offset };
  });
}

async function findeNamen(text) {
  const model = await ladeModell();
  const alle = [];
  for (const [offset, stueck] of stuecke(text)) {
    if (!stueck.trim()) continue;
    const tokens = await model(stueck, { ignore_labels: [] });
    alle.push(...entitaeten(stueck, tokens, offset));
  }
  return alle;
}

async function leseFoto({ bytes, lang }) {
  if (!tesseract) {
    const { createWorker } = (await import("./vendor/tesseract/tesseract.esm.min.js")).default;
    tesseract = await createWorker(lang.split("+"), 1, {
      workerPath: base + "vendor/tesseract/worker.min.js",
      corePath: base + "vendor/tesseract/core",
      langPath: base + "vendor/tesseract/lang",
      workerBlobURL: false,
      gzip: true,
    });
  }
  const { data: result } = await tesseract.recognize(new Blob([bytes], { type: "image/png" }));
  return result.text;
}

async function auftrag({ kind, payload }) {
  try {
    if (kind === "ner") antworte({ entities: await findeNamen(payload) });
    else if (kind === "ocr") antworte({ text: await leseFoto(payload) });
    else antworte({ error: `unbekannter Auftrag ${kind}` });
  } catch (err) {
    console.warn("FRITS-Helfer:", kind, err);
    antworte({ error: String(err && err.message ? err.message : err) });
  }
}

const imWorker = typeof WorkerGlobalScope !== "undefined"; // false when tests import this file
if (imWorker) self.onmessage = (event) => {
  const m = event.data;
  if (m.type === "start") {
    ({ base, ctl, data } = m);
    m.port.onmessage = (e) => auftrag(e.data);
  } else if (m.type === "modell-vorladen") {
    ladeModell().catch(() => {});
  }
};

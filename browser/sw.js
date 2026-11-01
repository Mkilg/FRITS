/* FRITS im Browser: the service worker.
 *
 * 1. Every request of the app frame (…/app/…) is answered by FRITS itself, which runs in this
 *    tab (Pyodide). Nothing goes to a server: we pass the request to the FRITS tab and return
 *    its answer.
 * 2. It adds the two headers that let the page use SharedArrayBuffer ("cross-origin
 *    isolation"). GitHub Pages can't send them, so the service worker does.
 * 3. It keeps a copy of the program files, so FRITS also starts without internet.
 */
"use strict";

const BASE = new URL("./", self.location).href;
const APP = BASE + "app/";
const CACHE = "frits-programm";
let shellId = null;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "ich-bin-frits") shellId = event.source.id;
});

function isolated(response) {
  // Opaque or error responses can't be copied; pass them on as they are.
  if (!response || response.type === "opaque" || response.type === "opaqueredirect") return response;
  const headers = new Headers(response.headers);
  headers.set("Cross-Origin-Embedder-Policy", "require-corp");
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  headers.set("Cross-Origin-Resource-Policy", "same-origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

function stamp(response) {
  const h = response.headers;
  return [h.get("etag"), h.get("last-modified"), h.get("content-length")].join("|");
}

async function fromNetwork(request) {
  // Network first (so an update arrives), the saved copy when offline.
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok && request.method === "GET") {
      const saved = await cache.match(request);
      if (!saved || stamp(saved) !== stamp(response)) await cache.put(request, response.clone());
    }
    return isolated(response);
  } catch (err) {
    const saved = await cache.match(request, { ignoreSearch: false });
    if (saved) return isolated(saved);
    throw err;
  }
}

async function findShell() {
  if (shellId) {
    const known = await self.clients.get(shellId);
    if (known) return known;
  }
  const windows = await self.clients.matchAll({ type: "window" });
  for (const client of windows) {
    const url = new URL(client.url);
    if (client.frameType === "top-level" && (url.href.split("#")[0] === BASE || url.href.split("#")[0] === BASE + "index.html")) {
      const answer = await ask(client, { type: "bist-du-frits" }, 1500).catch(() => null);
      if (answer && answer.ja) {
        shellId = client.id;
        return client;
      }
    }
  }
  return null;
}

function ask(client, message, timeoutMs, transfer = []) {
  return new Promise((resolve, reject) => {
    const channel = new MessageChannel();
    const timer = timeoutMs ? setTimeout(() => reject(new Error("timeout")), timeoutMs) : null;
    channel.port1.onmessage = (e) => { if (timer) clearTimeout(timer); resolve(e.data); };
    client.postMessage(message, [channel.port2, ...transfer]);
  });
}

function page(title, text, status) {
  const html = `<!doctype html><meta charset="utf-8"><title>${title}</title>
<body style="font-family:-apple-system,system-ui,sans-serif;background:#f1e4c8;color:#2b2620;padding:2rem 16px;max-width:36rem;margin:auto">
<h1 style="font-family:Georgia,serif">${title}</h1><p>${text}</p></body>`;
  return isolated(new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8" } }));
}

async function toFrits(request) {
  const shell = await findShell();
  if (!shell) {
    return page("FRITS ist nicht offen", `Öffne <a href="${BASE}">FRITS</a> neu, dann geht es weiter.`, 503);
  }
  const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();
  const message = {
    type: "anfrage",
    method: request.method,
    url: request.url,
    headers: [...request.headers.entries()],
    body,
  };
  const answer = await ask(shell, message, 0, body ? [body] : []);
  if (answer.error) return page("Das hat nicht geklappt", answer.error, 500);
  const status = answer.status;
  const nullBody = status === 204 || status === 304 || (status >= 300 && status < 400);
  return isolated(new Response(nullBody ? null : answer.body, { status, headers: answer.headers }));
}

self.addEventListener("fetch", (event) => {
  const url = event.request.url;
  if (!url.startsWith(BASE)) return; // other sites (the AI, the model download): untouched
  if (url.startsWith(APP + "static/")) {
    // Pictures, styles and scripts of the app: straight from the files next to this page.
    const rest = url.slice((APP + "static/").length);
    event.respondWith(fromNetwork(new Request(BASE + "static/" + rest)));
    return;
  }
  if (url.startsWith(APP) || url === BASE + "app") {
    event.respondWith(toFrits(event.request));
    return;
  }
  event.respondWith(fromNetwork(event.request));
});

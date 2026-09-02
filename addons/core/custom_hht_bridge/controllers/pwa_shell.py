# -*- coding: utf-8 -*-
# License: LGPL-3
"""PWA shell endpoints: HTML, web manifest, service worker."""

from __future__ import annotations

import json
import logging
import re

from odoo import http
from odoo.http import request


_logger = logging.getLogger(__name__)

# Boot reports are attacker-influenced strings heading for the log, so they are
# stripped of CR/LF/TAB (log-injection) and hard-capped before being written.
_LOG_SCRUB = re.compile(r"[\r\n\t]+")


def _clean(value, limit):
    return _LOG_SCRUB.sub(" ", str(value))[:limit]


_MANIFEST = {
    "name": "Hub HHT",
    "short_name": "HHT",
    "start_url": "/hht/",
    "display": "standalone",
    "theme_color": "#1f2937",
    "background_color": "#111827",
    "icons": [
        {
            "src": "/custom_hht_bridge/static/src/pwa/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
        },
        {
            "src": "/custom_hht_bridge/static/src/pwa/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
        },
    ],
}


_SW_SOURCE = r"""
// Hub HHT Service Worker — precache + SWR + offline POST queue.
// Bump CACHE_NAME on every shell change: 'activate' purges every cache whose
// name differs, so this is what lets a fixed shell reach devices that already
// cached a broken one.
const CACHE_NAME = 'hht-shell-v2';
const PRECACHE = ['/hht/', '/hht/manifest.webmanifest'];
const DB_NAME = 'hht-offline';
const STORE = 'pending';

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

function openDb() {
    return new Promise((resolve, reject) => {
        const r = indexedDB.open(DB_NAME, 1);
        r.onupgradeneeded = () => {
            r.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        };
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
    });
}

async function enqueue(req) {
    const body = await req.clone().text();
    const db = await openDb();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).add({
            url: req.url,
            method: req.method,
            headers: [...req.headers],
            body: body,
            ts: Date.now(),
        });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

async function flushQueue() {
    const db = await openDb();
    const items = await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    for (const item of items) {
        try {
            const resp = await fetch(item.url, {
                method: item.method,
                headers: new Headers(item.headers),
                body: item.body,
            });
            if (resp.ok) {
                await new Promise((resolve, reject) => {
                    const tx = db.transaction(STORE, 'readwrite');
                    tx.objectStore(STORE).delete(item.id);
                    tx.oncomplete = () => resolve();
                    tx.onerror = () => reject(tx.error);
                });
            }
        } catch (e) {
            // network still down; retry later
            break;
        }
    }
}

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (event.request.method === 'POST' && url.pathname.startsWith('/api/hht/')) {
        event.respondWith(
            fetch(event.request.clone()).catch(async () => {
                await enqueue(event.request);
                return new Response(
                    JSON.stringify({ ok: true, queued: true }),
                    { status: 202, headers: { 'Content-Type': 'application/json' } }
                );
            })
        );
        return;
    }
    if (event.request.method === 'GET' && url.pathname.startsWith('/api/hht/')) {
        // Stale-while-revalidate.
        event.respondWith(
            caches.open(CACHE_NAME).then(async (cache) => {
                const cached = await cache.match(event.request);
                const networkPromise = fetch(event.request).then((resp) => {
                    if (resp.ok) cache.put(event.request, resp.clone());
                    return resp;
                }).catch(() => cached);
                return cached || networkPromise;
            })
        );
        return;
    }
    // Navigations are network-first: cache-first would pin a broken shell on
    // the device forever, since the HTML lives at a stable URL. Fall back to
    // cache only when the network is actually unavailable (the offline case).
    if (event.request.mode === 'navigate' || event.request.destination === 'document') {
        event.respondWith(
            fetch(event.request).then((resp) => {
                if (resp.ok) {
                    const copy = resp.clone();
                    caches.open(CACHE_NAME).then((c) => c.put(event.request, copy));
                }
                return resp;
            }).catch(() => caches.match(event.request).then((c) => c || caches.match('/hht/')))
        );
        return;
    }
    if (event.request.method === 'GET') {
        // Static assets are content-hashed, so cache-first is safe here.
        event.respondWith(
            caches.match(event.request).then((cached) => cached || fetch(event.request))
        );
    }
});

self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'flush') {
        event.waitUntil(flushQueue());
    }
});

self.addEventListener('sync', (event) => {
    if (event.tag === 'hht-flush') {
        event.waitUntil(flushQueue());
    }
});
"""


class HhtPwaShell(http.Controller):
    @http.route("/hht", type="http", auth="user", methods=["GET"], csrf=False)
    def hht_root(self, **_kw):
        return request.redirect("/hht/", code=301)

    @http.route("/hht/", type="http", auth="user", methods=["GET"], csrf=False)
    def hht_shell(self, **_kw):
        return request.render(
            "custom_hht_bridge.hht_shell_layout",
            {
                "session_info": request.env["ir.http"].session_info(),
                "debug": request.session.debug,
                "json": json,
            },
        )

    @http.route(
        "/hht/manifest.webmanifest",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def hht_manifest(self, **_kw):
        body = json.dumps(_MANIFEST, separators=(",", ":"))
        return request.make_response(
            body,
            headers=[
                ("Content-Type", "application/manifest+json"),
                ("Cache-Control", "public, max-age=3600"),
            ],
        )

    @http.route(
        "/hht/boot-report",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def hht_boot_report(self, **_kw):
        """Receive a boot failure from a device that has no usable DevTools.

        Handhelds (Zebra/Denso) cannot show a console, so the shell's ES5 boot
        trap POSTs here instead. Fire-and-forget: never raise, never make the
        original failure worse.
        """
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or "{}")
        except ValueError:
            payload = {}
        errors = payload.get("errors") or []
        if not isinstance(errors, list):
            errors = [errors]
        _logger.warning(
            "HHT boot failure | user=%s | ua=%s | url=%s | errors=%s",
            _clean(request.env.user.login, 64),
            _clean(payload.get("ua", ""), 300),
            _clean(payload.get("url", ""), 200),
            " || ".join(_clean(e, 300) for e in errors[:5]) or "(none reported)",
        )
        return request.make_response("", headers=[("Content-Type", "text/plain")])

    @http.route("/hht/sw.js", type="http", auth="public", methods=["GET"], csrf=False)
    def hht_service_worker(self, **_kw):
        return request.make_response(
            _SW_SOURCE,
            headers=[
                ("Content-Type", "application/javascript; charset=utf-8"),
                ("Cache-Control", "no-cache"),
                ("Service-Worker-Allowed", "/"),
            ],
        )

/** @odoo-module **/
// License: LGPL-3
// IndexedDB-backed offline operation queue for the HHT shell.

const DB_NAME = "hht-shell";
const STORE = "ops";
const META_STORE = "meta";
const DB_VERSION = 1;

let _dbPromise = null;

export function open() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
        const r = indexedDB.open(DB_NAME, DB_VERSION);
        r.onupgradeneeded = () => {
            const db = r.result;
            if (!db.objectStoreNames.contains(STORE)) {
                db.createObjectStore(STORE, { keyPath: "client_id" });
            }
            if (!db.objectStoreNames.contains(META_STORE)) {
                db.createObjectStore(META_STORE, { keyPath: "k" });
            }
        };
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
    });
    return _dbPromise;
}

function _uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === "x" ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
}

async function _getBatchId(db) {
    return new Promise((resolve) => {
        const tx = db.transaction(META_STORE, "readwrite");
        const store = tx.objectStore(META_STORE);
        const req = store.get("batch_id");
        req.onsuccess = () => {
            let v = req.result && req.result.v;
            if (!v) {
                v = _uuid();
                store.put({ k: "batch_id", v });
            }
            resolve(v);
        };
        req.onerror = () => resolve(_uuid());
    });
}

export async function enqueue(op) {
    const db = await open();
    const batch_id = await _getBatchId(db);
    const record = {
        client_id: op.client_id || _uuid(),
        batch_id,
        ts: Date.now(),
        payload: op,
    };
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).put(record);
        tx.oncomplete = () => resolve(record);
        tx.onerror = () => reject(tx.error);
    });
}

export async function count() {
    const db = await open();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readonly");
        const req = tx.objectStore(STORE).count();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

export async function all() {
    const db = await open();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readonly");
        const req = tx.objectStore(STORE).getAll();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror = () => reject(req.error);
    });
}

async function _remove(db, client_ids) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readwrite");
        const store = tx.objectStore(STORE);
        client_ids.forEach((id) => store.delete(id));
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

async function _resetBatch(db) {
    return new Promise((resolve) => {
        const tx = db.transaction(META_STORE, "readwrite");
        tx.objectStore(META_STORE).put({ k: "batch_id", v: _uuid() });
        tx.oncomplete = () => resolve();
    });
}

/**
 * Flush the queue to /api/hht/sync.
 * @param {function} signedFetch  async (url, payload) => Response
 */
export async function flush(signedFetch) {
    const db = await open();
    const items = await all();
    if (!items.length) return { sent: 0, results: [] };
    const batch_id = items[0].batch_id;
    const payload = {
        batch_id,
        items: items.map((it) => ({
            client_id: it.client_id,
            ...it.payload,
        })),
    };
    let resp;
    try {
        resp = await signedFetch("/api/hht/sync", payload);
    } catch (e) {
        return { sent: 0, error: String(e) };
    }
    if (!resp.ok) return { sent: 0, error: `HTTP ${resp.status}` };
    let data = {};
    try { data = await resp.json(); } catch (_e) { /* ignore */ }
    const okIds = (data.results || [])
        .filter((r) => r.ok)
        .map((r) => r.client_id);
    if (okIds.length) await _remove(db, okIds);
    if (okIds.length === items.length) await _resetBatch(db);
    return { sent: okIds.length, results: data.results || [] };
}

let _interval = null;
export function startAutoFlush(signedFetch, intervalMs = 30000) {
    stopAutoFlush();
    const tick = () => { flush(signedFetch).catch(() => {}); };
    window.addEventListener("online", tick);
    _interval = setInterval(tick, intervalMs);
}

export function stopAutoFlush() {
    if (_interval) {
        clearInterval(_interval);
        _interval = null;
    }
}

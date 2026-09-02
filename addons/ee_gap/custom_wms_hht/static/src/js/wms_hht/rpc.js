/** @odoo-module **/
// License: LGPL-3

/**
 * Minimal JSON-RPC client for the /hht/wms/* endpoints.
 *
 * Deliberately not @web/core/network/rpc: that service raises on a business
 * error and pops a desktop dialog, which on a handheld reads as a frozen
 * screen. Here every endpoint answers {ok: false, error} and the caller shows
 * it in the page's own error strip.
 */
export async function call(route, params = {}) {
    const response = await fetch(route, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
    });
    if (!response.ok) {
        return { ok: false, error: `HTTP ${response.status}`, error_code: "HTTP" };
    }
    const payload = await response.json();
    if (payload.error) {
        const data = payload.error.data || {};
        return { ok: false, error: data.message || payload.error.message, error_code: "SERVER" };
    }
    return payload.result || { ok: false, error: "empty response", error_code: "EMPTY" };
}

/** Format a number the way a warehouse reads it: no trailing .00 noise. */
export function qty(value) {
    const n = Number(value || 0);
    return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

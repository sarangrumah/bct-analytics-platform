/** @odoo-module **/
// License: LGPL-3
// HMAC-SHA256 request signing using SubtleCrypto.

function _toHex(buffer) {
    const bytes = new Uint8Array(buffer);
    let hex = "";
    for (let i = 0; i < bytes.length; i++) {
        const b = bytes[i].toString(16).padStart(2, "0");
        hex += b;
    }
    return hex;
}

/**
 * Sign an HTTP request body with HMAC-SHA256.
 * @param {string} secret  shared device secret (hex string is fine — used as raw bytes)
 * @param {string} body    canonical body: "<timestamp><raw json>"
 * @returns {Promise<string>} hex digest
 */
export async function signRequest(secret, body) {
    if (!secret) {
        throw new Error("HHT: missing device secret for HMAC signing");
    }
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
        "raw",
        enc.encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
    );
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(body));
    return _toHex(sig);
}

/**
 * Build canonical signing string + headers.
 * @param {string} secret
 * @param {object} payload
 * @returns {Promise<{body: string, headers: object}>}
 */
export async function buildSignedRequest(secret, payload) {
    const ts = Math.floor(Date.now() / 1000).toString();
    const body = JSON.stringify(payload || {});
    const sig = await signRequest(secret, ts + body);
    return {
        body,
        headers: {
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": sig,
        },
    };
}

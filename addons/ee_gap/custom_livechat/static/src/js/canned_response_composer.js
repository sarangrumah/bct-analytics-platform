/** @odoo-module **/

/**
 * Discuss composer hook: expand `:shortcut` tokens via
 * `custom.livechat.canned.response.expand_canned`.
 *
 * MVP backend-mirroring helper: we expose a function on `window` so that the
 * discuss composer (and any e2e test) can call it. A full composer patch
 * lives in `custom_livechat.composer_patch` (intentionally not registered
 * here to avoid hard-coupling with private composer internals); this asset
 * is the public, stable surface.
 */

import { rpc } from "@web/core/network/rpc";

const SHORTCUT_RE = /(^|\s):([a-zA-Z0-9_-]{2,})\s*$/;

/**
 * Replace a trailing `:shortcut` token in `text` with the canned body
 * fetched from the server.
 *
 * @param {string} text - The current composer text.
 * @returns {Promise<{text: string, expanded: boolean, name: string}>}
 */
export async function expandCannedShortcut(text) {
    if (!text) {
        return { text: text || "", expanded: false, name: "" };
    }
    const match = text.match(SHORTCUT_RE);
    if (!match) {
        return { text, expanded: false, name: "" };
    }
    const shortcut = match[2];
    try {
        const result = await rpc("/web/dataset/call_kw", {
            model: "custom.livechat.canned.response",
            method: "expand_canned",
            args: [shortcut],
            kwargs: {},
        });
        if (!result || !result.found) {
            return { text, expanded: false, name: "" };
        }
        const before = text.slice(0, match.index + match[1].length);
        const expanded = before + (result.body || "");
        return { text: expanded, expanded: true, name: result.name || "" };
    } catch (_err) {
        return { text, expanded: false, name: "" };
    }
}

// Expose for ad-hoc/manual wiring on the composer.
if (typeof window !== "undefined") {
    window.customLivechatExpandCanned = expandCannedShortcut;
}

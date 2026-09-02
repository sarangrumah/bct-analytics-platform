/** @odoo-module **/

/**
 * "Insert into Reply" helper for the AI Suggested Reply textarea.
 *
 * Adds a click handler on `.o_custom_livechat_ai_reply` textareas that
 * copies the text to clipboard so agents can paste it into the discuss
 * composer.
 */

function copyAiReplyToClipboard(ev) {
    const el = ev.currentTarget;
    if (!el) {
        return;
    }
    const text = (el.value || el.textContent || "").trim();
    if (!text) {
        return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(() => {});
    } else {
        // Legacy fallback
        try {
            el.select();
            document.execCommand("copy");
        } catch (_err) {
            // ignore
        }
    }
}

function attach() {
    const nodes = document.querySelectorAll("textarea.o_custom_livechat_ai_reply");
    nodes.forEach((node) => {
        if (node.dataset.cannedClipboardBound === "1") {
            return;
        }
        node.dataset.cannedClipboardBound = "1";
        node.addEventListener("dblclick", copyAiReplyToClipboard);
        node.title = "Double-click to copy this AI reply into your clipboard.";
    });
}

if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", attach);
    } else {
        attach();
    }
    // Re-scan on DOM mutations (Odoo views re-render frequently).
    const observer = new MutationObserver(() => attach());
    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
    }
}

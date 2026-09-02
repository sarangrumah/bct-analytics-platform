/** @odoo-module **/
// License: LGPL-3

// Scan-burst detection. A wedge scanner emits keystrokes ~5-20ms apart; a
// thumb on a 4" on-screen keyboard is an order of magnitude slower. The idle
// gap has to clear the slowest scanner without making the operator wait.
export const BURST_IDLE_MS = 120;
export const BURST_MAX_GAP_MS = 45;
export const MIN_BURST_LEN = 4;

/** Old Android WebViews are a supported target here; not all have `performance`. */
export function now() {
    return typeof performance !== "undefined" && performance.now
        ? performance.now()
        : Date.now();
}

/**
 * Did this text arrive from a scanner rather than from a thumb?
 *
 * The Denso BHT units on the floor can be configured with no suffix: the
 * barcode lands in the input and nothing happens, which reads as "the app
 * ignored my scan". Submitting on a burst makes the terminator optional.
 * Human typing must never trigger it — a half-typed code sent early is worse
 * than no auto-submit at all — so the median inter-key gap decides, and
 * anything slow is left for the Go button.
 *
 * @param {number[]} times timestamps of the keystrokes, oldest first
 * @param {number} codeLength length of the text currently in the box
 */
export function isScanBurst(times, codeLength) {
    if (codeLength < MIN_BURST_LEN || times.length < MIN_BURST_LEN) return false;
    const gaps = times.slice(1).map((t, i) => t - times[i]).sort((a, b) => a - b);
    if (!gaps.length) return false;
    // Median, not mean: one stray pause (the scanner's own inter-character
    // delay, a GC hitch) must not disqualify an otherwise machine-speed burst.
    const median = gaps[Math.floor(gaps.length / 2)];
    return median <= BURST_MAX_GAP_MS;
}

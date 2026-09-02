/** @odoo-module **/
/*
 * Studio overlay service — global reactive flag toggled by the systray
 * paintbrush. When ``state.active`` is true, the StudioOverlay component
 * mounts decorations on the currently-rendered view (sidebar + field
 * highlights + drop targets) without disturbing the underlying view.
 */
import { registry } from "@web/core/registry";
import { reactive } from "@odoo/owl";

const studioOverlayService = {
    start() {
        const state = reactive({ active: false });
        return {
            state,
            toggle() {
                state.active = !state.active;
            },
            activate() {
                state.active = true;
            },
            deactivate() {
                state.active = false;
            },
        };
    },
};

registry.category("services").add("studio_overlay", studioOverlayService);

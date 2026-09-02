/** @odoo-module **/
/*
 * Studio systray — paintbrush button in the top bar that opens the
 * visual view editor scoped to whichever view is currently visible.
 *
 * Phase 4a deliverable. We don't (yet) do true inline drag-drop on the
 * live view like Enterprise Studio; instead this is a one-click route
 * from any record/list/kanban/form straight into our existing OWL
 * editor, with the current view + model pre-selected. The user edits,
 * saves, navigates back via breadcrumb — the original page reloads
 * picking up the new inheritance.
 */
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class StudioSystrayItem extends Component {
    static template = "custom_studio_lite.StudioSystrayItem";
    static props = {};

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.overlay = useService("studio_overlay");
        // Subscribe to the reactive service state — without useState the
        // button's "active" highlight wouldn't re-render on toggle.
        this.overlayState = useState(this.overlay.state);
    }

    /** When clicked over an editable view, toggle the inline overlay.
     *  When no view is in the current controller, fall through to the
     *  standalone editor so the user can still pick a target.
     */
    onClick() {
        const ctrl = this.action.currentController;
        const hasResModel = ctrl && ctrl.props && ctrl.props.resModel;
        if (hasResModel) {
            this.overlay.toggle();
            return;
        }
        // Fallback — no current view, open the standalone editor menu.
        this.notification.add(
            _t("Open a list / form / kanban first to enter inline Studio mode. Opening the picker…"),
            { type: "info" },
        );
        this.action.doAction({
            type: "ir.actions.client",
            tag: "custom_studio_lite.studio_view_editor",
            name: _t("Studio — Visual View Editor"),
            target: "main",
        });
    }

    /** Visual feedback in the systray button when studio mode is on. */
    get active() {
        return this.overlayState.active;
    }
}

// Add to the systray. ``sequence: 1`` puts us near the right side of
// the navbar (lower sequence = righter — same convention Odoo uses).
registry.category("systray").add(
    "custom_studio_lite.systray_studio_button",
    {
        Component: StudioSystrayItem,
    },
    { sequence: 1 },
);

/** @odoo-module **/
/*
 * Studio Visual View Editor — Phase 2 OWL client action.
 *
 * Lets a designer pick a model + view, see the field layout, and apply
 * operations (add field, hide field, move field, set attribute) which
 * are saved as a studio.view.customization record. The actual XPath
 * inheritance is generated server-side in studio.view.customization
 * model — this UI only composes the operation list.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class StudioViewEditor extends Component {
    static template = "custom_studio_lite.StudioViewEditor";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");

        this.state = useState({
            loading: true,
            models: [],
            modelId: null,
            modelName: "",
            views: [],
            viewId: null,
            view: null,
            fieldNodes: [],
            availableFields: [],
            operations: [],
            customizationId: null,
            saving: false,
            error: null,
        });

        onWillStart(async () => {
            await this._loadModels();
            await this._applyContextDefaults();
        });
    }

    /**
     * If launched via the systray (or any caller passing default_model /
     * default_view_id in the action context), pre-populate the model +
     * view dropdowns so the user lands directly on the field editor.
     */
    async _applyContextDefaults() {
        const ctx = (this.props.action && this.props.action.context) || {};
        const defaultModel = ctx.default_model;
        const defaultViewId = ctx.default_view_id;
        if (!defaultModel) return;
        const model = this.state.models.find((m) => m.model === defaultModel);
        if (!model) return;
        await this.onModelChange({ target: { value: String(model.id) } });
        if (defaultViewId && this.state.views.some((v) => v.id === defaultViewId)) {
            await this.onViewChange({ target: { value: String(defaultViewId) } });
        } else if (this.state.views.length) {
            // Pick the first form view if no specific view was passed.
            const form = this.state.views.find((v) => v.type === "form");
            const candidate = form || this.state.views[0];
            await this.onViewChange({ target: { value: String(candidate.id) } });
        }
    }

    async _loadModels() {
        try {
            // Materialised studio.custom.field gives us the list of models
            // that already have custom fields — start there for relevance.
            const models = await this.orm.searchRead(
                "ir.model",
                [["transient", "=", false], ["abstract", "=", false]],
                ["id", "model", "name"],
                { order: "name", limit: 200 },
            );
            this.state.models = models;
            this.state.loading = false;
        } catch (e) {
            this.state.error = e.message || String(e);
            this.state.loading = false;
        }
    }

    async onModelChange(ev) {
        const modelId = Number(ev.target.value) || null;
        this.state.modelId = modelId;
        this.state.viewId = null;
        this.state.view = null;
        this.state.fieldNodes = [];
        this.state.operations = [];
        this.state.customizationId = null;
        if (!modelId) return;
        const model = this.state.models.find((m) => m.id === modelId);
        this.state.modelName = model ? model.model : "";

        const views = await this.orm.searchRead(
            "ir.ui.view",
            [
                ["model", "=", this.state.modelName],
                ["type", "in", ["form", "list", "kanban", "search"]],
                ["inherit_id", "=", false],
            ],
            ["id", "name", "type"],
            { order: "type, name", limit: 100 },
        );
        this.state.views = views;
    }

    async onViewChange(ev) {
        const viewId = Number(ev.target.value) || null;
        this.state.viewId = viewId;
        this.state.operations = [];
        this.state.customizationId = null;
        if (!viewId) {
            this.state.view = null;
            this.state.fieldNodes = [];
            return;
        }
        const [view] = await this.orm.read(
            "ir.ui.view",
            [viewId],
            ["id", "name", "type", "arch_db"],
        );
        this.state.view = view;
        this.state.fieldNodes = this._extractFieldNodes(view.arch_db || "");
        await this._loadAvailableFields();
        await this._loadExistingCustomization();
    }

    _extractFieldNodes(arch) {
        // Parse arch_db with DOMParser and walk for <field> nodes that
        // carry a name attribute. Returns a flat ordered list — the
        // visual order is the document order which is good enough for
        // Phase 2 (no nested group visualisation yet).
        const nodes = [];
        try {
            const doc = new DOMParser().parseFromString(arch, "application/xml");
            const fields = doc.getElementsByTagName("field");
            for (const f of fields) {
                const name = f.getAttribute("name");
                if (!name) continue;
                nodes.push({
                    name,
                    string: f.getAttribute("string") || name,
                    widget: f.getAttribute("widget") || "",
                    invisible: f.getAttribute("invisible") || "",
                    readonly: f.getAttribute("readonly") || "",
                    required: f.getAttribute("required") || "",
                });
            }
        } catch (e) {
            this.notification.add(_t("Could not parse view arch: %s", e.message), {
                type: "danger",
            });
        }
        return nodes;
    }

    async _loadAvailableFields() {
        // List all fields on the model that are not yet present in the view.
        const allFields = await this.orm.searchRead(
            "ir.model.fields",
            [["model", "=", this.state.modelName]],
            ["id", "name", "field_description", "ttype"],
            { order: "field_description", limit: 500 },
        );
        const inViewNames = new Set(this.state.fieldNodes.map((n) => n.name));
        this.state.availableFields = allFields.filter(
            (f) => !inViewNames.has(f.name),
        );
    }

    async _loadExistingCustomization() {
        const existing = await this.orm.searchRead(
            "studio.view.customization",
            [["target_view_id", "=", this.state.viewId]],
            ["id", "name", "state"],
            { limit: 1, order: "id desc" },
        );
        if (existing.length) {
            this.state.customizationId = existing[0].id;
            const ops = await this.orm.searchRead(
                "studio.view.operation",
                [["customization_id", "=", existing[0].id]],
                [
                    "id", "sequence", "op_type", "field_name",
                    "anchor_field", "position", "attr_name", "attr_value",
                ],
                { order: "sequence" },
            );
            this.state.operations = ops.map((o) => ({ ...o, draftId: null }));
        }
    }

    addOperation(opType, fieldName) {
        const seq = (this.state.operations.length + 1) * 10;
        const op = {
            id: null,
            draftId: `draft-${Date.now()}-${Math.random()}`,
            sequence: seq,
            op_type: opType,
            field_name: fieldName || "",
            anchor_field: "",
            position: "after",
            attr_name: "",
            attr_value: "",
        };
        this.state.operations = [...this.state.operations, op];
    }

    removeOperation(idx) {
        const next = [...this.state.operations];
        next.splice(idx, 1);
        this.state.operations = next;
    }

    moveOperation(idx, direction) {
        const next = [...this.state.operations];
        const newIdx = idx + direction;
        if (newIdx < 0 || newIdx >= next.length) return;
        [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
        next.forEach((o, i) => (o.sequence = (i + 1) * 10));
        this.state.operations = next;
    }

    updateOperation(idx, key, value) {
        const next = [...this.state.operations];
        next[idx] = { ...next[idx], [key]: value };
        this.state.operations = next;
    }

    onAddFieldClick(fieldName) {
        const anchor =
            this.state.fieldNodes.length > 0
                ? this.state.fieldNodes[this.state.fieldNodes.length - 1].name
                : "";
        const op = {
            id: null,
            draftId: `draft-${Date.now()}-${Math.random()}`,
            sequence: (this.state.operations.length + 1) * 10,
            op_type: "add_field",
            field_name: fieldName,
            anchor_field: anchor,
            position: "after",
            attr_name: "",
            attr_value: "",
        };
        this.state.operations = [...this.state.operations, op];
    }

    onHideFieldClick(fieldName) {
        this.state.operations = [
            ...this.state.operations,
            {
                id: null,
                draftId: `draft-${Date.now()}-${Math.random()}`,
                sequence: (this.state.operations.length + 1) * 10,
                op_type: "hide_field",
                field_name: fieldName,
                anchor_field: "",
                position: "after",
                attr_name: "",
                attr_value: "",
            },
        ];
    }

    // ----- Drag & drop -----
    //
    // The available-fields panel is a drag source; the in-view list is
    // the drop target. Dropping on a specific row creates an add_field
    // op anchored at that row (position=after). Dropping on the trailing
    // sentinel appends at the end of the form.

    onDragStartField(fieldName, ev) {
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData("application/x-studio-field", fieldName);
        // Some browsers refuse the drop without a text/plain payload too.
        ev.dataTransfer.setData("text/plain", fieldName);
    }

    onDragOverViewSlot(ev) {
        // Allow the drop — without preventDefault, the drop event fires nowhere.
        if (ev.dataTransfer.types.includes("application/x-studio-field")) {
            ev.preventDefault();
            ev.dataTransfer.dropEffect = "copy";
        }
    }

    onDropOnViewSlot(anchorName, ev) {
        ev.preventDefault();
        const fieldName =
            ev.dataTransfer.getData("application/x-studio-field") ||
            ev.dataTransfer.getData("text/plain");
        if (!fieldName) return;
        const lastFieldName =
            this.state.fieldNodes.length > 0
                ? this.state.fieldNodes[this.state.fieldNodes.length - 1].name
                : "";
        this.state.operations = [
            ...this.state.operations,
            {
                id: null,
                draftId: `draft-${Date.now()}-${Math.random()}`,
                sequence: (this.state.operations.length + 1) * 10,
                op_type: "add_field",
                field_name: fieldName,
                anchor_field: anchorName || lastFieldName,
                position: anchorName ? "after" : "inside",
                attr_name: "",
                attr_value: "",
            },
        ];
        this.notification.add(
            _t("Queued: add %s after %s", fieldName, anchorName || "(end of view)"),
            { type: "info" },
        );
    }

    /** Close the editor and return to the previous page via the action service. */
    async cancel() {
        // restore: same intent as breadcrumb "back" — uses Odoo's history.
        const restored = this.action.restore();
        if (!restored) {
            // No previous action in the stack — fall back to home.
            this.action.doAction({ type: "ir.actions.act_window_close" });
        }
    }

    async save() {
        if (!this.state.viewId || !this.state.operations.length) {
            this.notification.add(
                _t("Select a view and add at least one operation."),
                { type: "warning" },
            );
            return;
        }
        this.state.saving = true;
        try {
            // Strip client-only fields before persisting.
            const opPayload = this.state.operations.map((o, i) => {
                const { id, draftId, ...rest } = o;
                rest.sequence = (i + 1) * 10;
                return rest;
            });

            let custId = this.state.customizationId;
            if (!custId) {
                custId = await this.orm.create("studio.view.customization", [
                    {
                        name: `${this.state.view.name} — visual edits`,
                        target_view_id: this.state.viewId,
                        operation_ids: opPayload.map((o) => [0, 0, o]),
                    },
                ]);
                custId = Array.isArray(custId) ? custId[0] : custId;
                this.state.customizationId = custId;
            } else {
                // Replace operations: drop all existing, re-create from current state.
                const existingOps = await this.orm.searchRead(
                    "studio.view.operation",
                    [["customization_id", "=", custId]],
                    ["id"],
                );
                if (existingOps.length) {
                    await this.orm.unlink(
                        "studio.view.operation",
                        existingOps.map((o) => o.id),
                    );
                }
                await this.orm.write("studio.view.customization", [custId], {
                    operation_ids: opPayload.map((o) => [0, 0, o]),
                });
            }
            await this.orm.call("studio.view.customization", "action_apply", [
                [custId],
            ]);
            // Re-read state to confirm apply succeeded.
            const [persisted] = await this.orm.read(
                "studio.view.customization",
                [custId],
                ["state", "last_error"],
            );
            if (persisted.state === "applied") {
                this.notification.add(_t("Customization applied."), {
                    type: "success",
                });
                // Refresh field nodes from the now-extended combined arch.
                await this.onViewChange({ target: { value: this.state.viewId } });
            } else {
                this.notification.add(
                    _t("Apply failed: %s", persisted.last_error || "unknown"),
                    { type: "danger", sticky: true },
                );
            }
        } catch (e) {
            this.notification.add(_t("Save failed: %s", e.message || String(e)), {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.saving = false;
        }
    }

    async openCustomization() {
        if (!this.state.customizationId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "studio.view.customization",
            res_id: this.state.customizationId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add(
    "custom_studio_lite.studio_view_editor",
    StudioViewEditor,
);

/** @odoo-module **/
/*
 * Studio overlay — Phase 4b.
 *
 * When the systray flips studio mode on, this component:
 *   1. Reads the currently active view via the action service
 *      (resModel + viewId from action.currentController.props).
 *   2. Slides a sidebar in from the right listing all fields on the
 *      model that are not already on the current view (drag sources).
 *   3. Installs a document-level drag-over/drop delegator that
 *      paints drop indicators on existing rendered fields
 *      (``.o_field_widget[name]``) and queues an add_field op
 *      anchored after whichever field receives the drop.
 *   4. Persists each drop as a studio.view.customization operation
 *      via the same backend used by Phase 2 — then reloads the
 *      action so the form re-renders with the new arch.
 *
 * Caveats vs. Enterprise Studio:
 *   - We don't (yet) detect group / notebook ancestry; the op anchor
 *     is just the field name and we rely on the server-side
 *     ``<xpath expr="//field[@name='anchor']" position="after">``.
 *     For Phase 4b that's good enough; Phase 4c can add nested
 *     group/page resolution.
 *   - No properties panel / no inline label editing — drop-only.
 *   - Click-to-hide still goes through the click handler on
 *     decorated fields.
 */
import { Component, useEffect, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { rpcBus } from "@web/core/network/rpc";

const FIELD_SELECTOR = ".o_field_widget[name]";

export class StudioOverlay extends Component {
    static template = "custom_studio_lite.StudioOverlay";
    static props = {};

    setup() {
        this.overlayService = useService("studio_overlay");
        this.action = useService("action");
        this.orm = useService("orm");
        this.notification = useService("notification");

        // OWL reactivity rule: a component only re-renders when it
        // accesses the reactive via its OWN useState wrapper. Without
        // this line, toggling overlayService.state.active would mutate
        // the value but never trigger a re-render here.
        this.overlayState = useState(this.overlayService.state);

        this.state = useState({
            currentModel: null,
            currentModelId: null,
            currentViewId: null,
            currentViewName: "",
            availableFields: [],
            inViewFields: [],
            filter: "",
            saving: false,
            selectedField: null,
            // True when the selected name only exists as a widget-rendered
            // element (no <field> in arch) — properties UI hides edit
            // affordances in that case, only Hide is offered.
            selectedFieldIsWidgetOnly: false,
            // Names rendered in the DOM but not present in arch as <field>.
            renderedOnlyNames: [],
            // Fields that sit inside a <group> — preferred default
            // placement anchors so new fields render with proper labels.
            groupFieldNames: [],
            // Persisted op listing (third tab for cleanup).
            operations: [],
            customizationId: null,
            // Properties panel state — populated when a field is clicked.
            tab: "add", // 'add' | 'properties' | 'ops'
            fieldProps: {
                label: "",
                widget: "",
                invisible: false,
                readonly: false,
                required: false,
            },
            fieldPropsOriginal: null,
            isStudioField: false,
            studioFieldId: null,
            // Inline new-field creator.
            newFieldLabel: "",
            newFieldType: "char",
        });

        // React to studio-mode toggle: initialise + install listeners
        // when active, tear them down when not.
        useEffect(
            () => {
                if (this.isActive) {
                    document.body.classList.add("o_studio_active");
                    this._initialize();
                    const cleanup = this._installDelegatedListeners();
                    return () => {
                        document.body.classList.remove("o_studio_active");
                        cleanup();
                    };
                }
                return () => {};
            },
            () => [this.isActive],
        );
    }

    get isActive() {
        return this.overlayState.active;
    }

    /** Snapshot the current view + field lists when studio mode is enabled. */
    async _initialize() {
        const ctrl = this.action.currentController;
        if (!ctrl || !ctrl.props || !ctrl.props.resModel) {
            this.notification.add(
                _t("Open a form / list / kanban first, then activate Studio."),
                { type: "warning" },
            );
            this.overlayService.deactivate();
            return;
        }
        this.state.currentModel = ctrl.props.resModel;
        this.state.currentViewId = await this._resolveViewId(ctrl);
        const viewType = this._viewType(ctrl);
        this.state.currentViewName =
            (viewType ? `[${viewType}] ` : "") +
            (ctrl.title || ctrl.displayName || "");
        // Cache the ir.model id so the New Field creator can target it.
        try {
            const m = await this.orm.searchRead(
                "ir.model",
                [["model", "=", ctrl.props.resModel]],
                ["id"],
                { limit: 1 },
            );
            this.state.currentModelId = m.length ? m[0].id : null;
        } catch (e) {
            this.state.currentModelId = null;
        }

        // Fields that exist as <field name="..."> in the combined arch.
        // These are the only ones we can safely target with XPath ops —
        // widget-rendered names (e.g. web_ribbon uses ``active`` in an
        // invisible expression but emits no <field> node) would crash
        // ``apply_inheritance_specs`` with "cannot be located in parent
        // view". Falls back to a DOM scan if the arch read fails.
        const archFieldNames = await this._readArchFieldNames();
        const renderedNames = this._readRenderedFieldNames();
        this.state.inViewFields = archFieldNames.length
            ? archFieldNames
            : renderedNames;
        // Names that show up on screen but aren't real <field> nodes
        // (widget-rendered, button targets, etc.). We can still HIDE
        // these via hide_field (now uses //*[@name='X'] server-side),
        // but cannot edit label/widget/etc. on them.
        const archSet = new Set(archFieldNames);
        this.state.renderedOnlyNames = renderedNames.filter(
            (n) => !archSet.has(n),
        );

        // All fields on the model, sourced from ir.model.fields.
        const all = await this.orm.searchRead(
            "ir.model.fields",
            [["model", "=", this.state.currentModel]],
            ["id", "name", "field_description", "ttype"],
            { order: "field_description", limit: 500 },
        );
        const displayedSet = new Set(this.state.inViewFields);
        this.state.availableFields = all.filter((f) => !displayedSet.has(f.name));
        // Also load the customization's ops so the Ops tab is in sync.
        await this.loadOperations();
    }

    /** Pick out the view *type* from a controller. Odoo 19 standard view
     *  props expose this as ``props.type`` (NOT ``viewType``), and the
     *  controller itself also carries a ``view`` object whose ``.type``
     *  is canonical. */
    _viewType(ctrl) {
        return (
            (ctrl.props && ctrl.props.type) ||
            (ctrl.view && ctrl.view.type) ||
            null
        );
    }

    /** Resolve the view id of the currently-rendered view. The Odoo 19
     *  controller exposes the *resolved* view as ``ctrl.view`` (id +
     *  type), which is the cleanest source. We also keep fallbacks for
     *  edge cases (sub-action controllers, action.views matching, and
     *  finally an ir.ui.view RPC by (model, type)). */
    async _resolveViewId(ctrl) {
        // 1. The resolved view attached to the controller.
        if (ctrl.view && ctrl.view.id) return ctrl.view.id;
        // 2. action.views as [[id, type], ...].
        const viewType = this._viewType(ctrl);
        const fromAction =
            ctrl.action && (ctrl.action.views || []).find(([, type]) => type === viewType);
        if (fromAction && fromAction[0]) return fromAction[0];
        // 3. controller.views (an array of view objects).
        const fromViews =
            ctrl.views && ctrl.views.find((v) => v.type === viewType);
        if (fromViews && fromViews.id) return fromViews.id;
        // 4. Last resort: RPC for the primary view of (model, type).
        if (!viewType) return null;
        try {
            const rows = await this.orm.searchRead(
                "ir.ui.view",
                [
                    ["model", "=", ctrl.props.resModel],
                    ["type", "=", viewType],
                    ["inherit_id", "=", false],
                ],
                ["id"],
                { limit: 1, order: "priority, id" },
            );
            return rows.length ? rows[0].id : null;
        } catch (e) {
            return null;
        }
    }

    _readRenderedFieldNames() {
        const names = [];
        for (const el of document.querySelectorAll(FIELD_SELECTOR)) {
            const n = el.getAttribute("name");
            if (n && !names.includes(n)) names.push(n);
        }
        return names;
    }

    /** Parse the combined arch for actual ``<field name=...>`` nodes —
     *  these are the only legal XPath anchors. Widget-rendered names
     *  (web_ribbon, button targets, etc.) are intentionally excluded.
     *  Also caches the names of fields rendered *inside a <group>* so
     *  we can prefer them as default anchors — placing a new field after
     *  a non-group field (like ``properties``) drops it into a flex
     *  container that doesn't render normal form widgets properly. */
    async _readArchFieldNames() {
        this.state.groupFieldNames = [];
        if (!this.state.currentViewId) return [];
        try {
            const arch = await this.orm.call(
                "ir.ui.view",
                "get_combined_arch",
                [[this.state.currentViewId]],
            );
            const doc = new DOMParser().parseFromString(arch, "application/xml");
            const out = [];
            for (const el of doc.getElementsByTagName("field")) {
                const n = el.getAttribute("name");
                if (n && !out.includes(n)) out.push(n);
            }
            // Walk groups specifically — the last <field> in the last
            // <group> is the safest default insertion point.
            const groupFields = [];
            for (const grp of doc.getElementsByTagName("group")) {
                for (const el of grp.getElementsByTagName("field")) {
                    const n = el.getAttribute("name");
                    if (n && !groupFields.includes(n)) groupFields.push(n);
                }
            }
            this.state.groupFieldNames = groupFields;
            return out;
        } catch (e) {
            return [];
        }
    }

    // ----- Sidebar drag source -----

    onDragStart(fieldName, ev) {
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData("application/x-studio-field", fieldName);
        ev.dataTransfer.setData("text/plain", fieldName);
    }

    // ----- Document-level drag delegate -----

    _installDelegatedListeners() {
        const onOver = (ev) => {
            if (!ev.dataTransfer.types.includes("application/x-studio-field")) return;
            const target = ev.target.closest(FIELD_SELECTOR);
            if (!target) return;
            ev.preventDefault();
            ev.dataTransfer.dropEffect = "copy";
            document
                .querySelectorAll(".o_studio_drag_over")
                .forEach((el) => el.classList.remove("o_studio_drag_over"));
            target.classList.add("o_studio_drag_over");
        };

        const onLeave = (ev) => {
            const target = ev.target.closest(FIELD_SELECTOR);
            if (target) target.classList.remove("o_studio_drag_over");
        };

        const onDrop = async (ev) => {
            if (!ev.dataTransfer.types.includes("application/x-studio-field")) return;
            const target = ev.target.closest(FIELD_SELECTOR);
            if (!target) return;
            ev.preventDefault();
            ev.stopPropagation();
            target.classList.remove("o_studio_drag_over");
            const newField =
                ev.dataTransfer.getData("application/x-studio-field") ||
                ev.dataTransfer.getData("text/plain");
            const anchor = target.getAttribute("name");
            // Same arch-vs-DOM caveat as the click handler.
            if (anchor && !this.state.inViewFields.includes(anchor)) {
                this.notification.add(
                    _t(
                        "Drop target '%s' isn't a real <field> node in the arch — pick a different anchor.",
                        anchor,
                    ),
                    { type: "warning" },
                );
                return;
            }
            if (newField && anchor) {
                await this._queueAddField(newField, anchor);
            }
        };

        // Field click → mark as selected + open properties tab. The
        // properties UI degrades gracefully for widget-rendered names:
        // edit affordances are hidden, only Hide is offered.
        const onFieldClick = async (ev) => {
            const target = ev.target.closest(FIELD_SELECTOR);
            if (!target) return;
            // Block normal click behaviour while studio mode is active —
            // we don't want the user accidentally opening relational
            // links / triggering button onclicks.
            ev.preventDefault();
            ev.stopPropagation();
            const fieldName = target.getAttribute("name");
            const inArch = this.state.inViewFields.includes(fieldName);
            const inRenderedOnly = this.state.renderedOnlyNames.includes(fieldName);
            if (!inArch && !inRenderedOnly) {
                // Truly unknown — shouldn't happen but degrade safely.
                this.notification.add(
                    _t("Field '%s' is not in this view's arch or DOM.", fieldName),
                    { type: "warning" },
                );
                return;
            }
            document
                .querySelectorAll(".o_studio_selected")
                .forEach((el) => el.classList.remove("o_studio_selected"));
            target.classList.add("o_studio_selected");
            this.state.selectedField = fieldName;
            this.state.selectedFieldIsWidgetOnly = !inArch;
            this.state.tab = "properties";
            if (inArch) {
                await this._loadFieldProperties(fieldName);
            } else {
                // Widget-only: we cannot read attrs from a <field> node.
                this.state.fieldProps = {
                    label: "",
                    widget: "",
                    invisible: false,
                    readonly: false,
                    required: false,
                };
                this.state.fieldPropsOriginal = null;
                this.state.isStudioField = false;
                this.state.studioFieldId = null;
            }
        };

        document.body.addEventListener("dragover", onOver);
        document.body.addEventListener("dragleave", onLeave);
        document.body.addEventListener("drop", onDrop);
        document.body.addEventListener("click", onFieldClick, true);

        return () => {
            document.body.removeEventListener("dragover", onOver);
            document.body.removeEventListener("dragleave", onLeave);
            document.body.removeEventListener("drop", onDrop);
            document.body.removeEventListener("click", onFieldClick, true);
            document
                .querySelectorAll(".o_studio_drag_over, .o_studio_selected")
                .forEach((el) =>
                    el.classList.remove("o_studio_drag_over", "o_studio_selected"),
                );
        };
    }

    // ----- Persist + apply -----

    async _queueAddField(fieldName, anchorName) {
        // Dedup: skip if the field is already part of the view.
        if (this.state.inViewFields.includes(fieldName)) {
            this.notification.add(
                _t("'%s' is already on this view — drag a different field.", fieldName),
                { type: "info" },
            );
            return;
        }
        // Re-resolve at drop-time in case the user dragged before
        // _initialize() finished, OR the active controller changed.
        if (!this.state.currentViewId) {
            const ctrl = this.action.currentController;
            if (ctrl && ctrl.props && ctrl.props.resModel) {
                this.state.currentViewId = await this._resolveViewId(ctrl);
            }
        }
        if (!this.state.currentViewId) {
            this.notification.add(
                _t(
                    "Cannot detect the current view id. Model=%s, type=%s — please report.",
                    this.state.currentModel || "?",
                    this._viewType(this.action.currentController) || "?",
                ),
                { type: "danger", sticky: true },
            );
            return;
        }
        // Anchor sanitation: a drop target outside any <group> (e.g. the
        // built-in ``properties`` flex container, status bars, button
        // boxes) yields a placement that the form renderer hides or
        // shoves into a flex row — looks like a silent failure to the
        // user. Re-route to the last field inside a real <group>.
        let safeAnchor = anchorName;
        if (anchorName && !this.state.groupFieldNames.includes(anchorName)) {
            const fallback =
                this.state.groupFieldNames[this.state.groupFieldNames.length - 1];
            if (fallback) {
                safeAnchor = fallback;
                this.notification.add(
                    _t(
                        "'%s' is not inside a group — placing after '%s' instead.",
                        anchorName,
                        fallback,
                    ),
                    { type: "info" },
                );
            }
        }
        await this._appendOpsAndApply([
            {
                op_type: "add_field",
                field_name: fieldName,
                anchor_field: safeAnchor,
                position: "after",
            },
        ]);
    }

    // ---------- Properties panel ----------

    /** Fetch the current attrs of a field from the combined view arch,
     *  plus whether it's a studio-created custom field. */
    async _loadFieldProperties(fieldName) {
        const blank = {
            label: "",
            widget: "",
            invisible: false,
            readonly: false,
            required: false,
        };
        this.state.fieldProps = { ...blank };
        this.state.fieldPropsOriginal = null;
        this.state.isStudioField = false;
        this.state.studioFieldId = null;
        if (!fieldName || !this.state.currentViewId) return;
        try {
            const combined = await this.orm.call(
                "ir.ui.view",
                "get_combined_arch",
                [[this.state.currentViewId]],
            );
            const doc = new DOMParser().parseFromString(combined, "application/xml");
            // First <field name="X"> wins — same heuristic the editor list uses.
            const fieldNode = doc.querySelector(
                `field[name="${fieldName.replace(/"/g, '\\"')}"]`,
            );
            if (fieldNode) {
                const truthy = (v) =>
                    v === "1" || v === "True" || v === "true" || v === true;
                this.state.fieldProps = {
                    label: fieldNode.getAttribute("string") || "",
                    widget: fieldNode.getAttribute("widget") || "",
                    invisible: truthy(fieldNode.getAttribute("invisible")),
                    readonly: truthy(fieldNode.getAttribute("readonly")),
                    required: truthy(fieldNode.getAttribute("required")),
                };
                this.state.fieldPropsOriginal = { ...this.state.fieldProps };
            }
            // Check if this is a studio.custom.field (so we can offer Delete).
            const custom = await this.orm.searchRead(
                "studio.custom.field",
                [
                    ["technical_name", "=", fieldName],
                    ["model_name", "=", this.state.currentModel],
                ],
                ["id"],
                { limit: 1 },
            );
            if (custom.length) {
                this.state.isStudioField = true;
                this.state.studioFieldId = custom[0].id;
            }
        } catch (e) {
            this.notification.add(
                _t("Could not load field properties: %s", e.message || String(e)),
                { type: "warning" },
            );
        }
    }

    /** Diff fieldProps vs original, persist each change as a set_attr op. */
    async applyFieldProperties() {
        if (!this.state.selectedField || !this.state.fieldPropsOriginal) return;
        const ops = [];
        const orig = this.state.fieldPropsOriginal;
        const curr = this.state.fieldProps;
        // String attrs.
        for (const key of ["label", "widget"]) {
            if ((curr[key] || "") !== (orig[key] || "")) {
                const attr = key === "label" ? "string" : key;
                ops.push({
                    op_type: "set_attr",
                    field_name: this.state.selectedField,
                    attr_name: attr,
                    attr_value: curr[key] || "",
                });
            }
        }
        // Boolean attrs — set_attr with "1" or "0". We always emit when
        // the value differs from original so the inheritance encodes the
        // explicit state.
        for (const key of ["invisible", "readonly", "required"]) {
            if (!!curr[key] !== !!orig[key]) {
                ops.push({
                    op_type: "set_attr",
                    field_name: this.state.selectedField,
                    attr_name: key,
                    attr_value: curr[key] ? "1" : "0",
                });
            }
        }
        if (!ops.length) {
            this.notification.add(_t("No changes to apply."), { type: "info" });
            return;
        }
        await this._appendOpsAndApply(ops);
    }

    /** Move the selected field one slot up or down by emitting a
     *  move_field op anchored at the adjacent field. */
    async moveSelectedField(direction) {
        const fieldName = this.state.selectedField;
        if (!fieldName) return;
        const idx = this.state.inViewFields.indexOf(fieldName);
        if (idx < 0) return;
        const targetIdx = direction === "up" ? idx - 1 : idx + 1;
        if (targetIdx < 0 || targetIdx >= this.state.inViewFields.length) return;
        const anchor = this.state.inViewFields[targetIdx];
        const position = direction === "up" ? "before" : "after";
        await this._appendOpsAndApply([
            {
                op_type: "move_field",
                field_name: fieldName,
                anchor_field: anchor,
                position,
            },
        ]);
    }

    /** Delete a studio.custom.field outright (force cascade — any
     *  remaining view refs are stripped by the field's unlink hook). */
    async deleteStudioField() {
        if (!this.state.isStudioField || !this.state.studioFieldId) return;
        const ok = window.confirm(
            _t("Delete the custom field '%s'? This drops the DB column.", this.state.selectedField),
        );
        if (!ok) return;
        this.state.saving = true;
        try {
            await this.orm.call(
                "studio.custom.field",
                "unlink",
                [[this.state.studioFieldId]],
                { context: { force_cascade: true } },
            );
            this.notification.add(_t("Field deleted — reloading view…"), {
                type: "success",
            });
            await this._reloadAction();
            this.state.selectedField = null;
            this.state.tab = "add";
            await this._initialize();
        } catch (e) {
            this.notification.add(_t("Delete failed: %s", e.message || String(e)), {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.saving = false;
        }
    }

    /** Quick-create a studio.custom.field via the sidebar, then place it
     *  on the current view. Uses the atomic server-side
     *  ``studio_create_and_place`` helper so the registry refresh that
     *  follows field creation is fully visible to the view validator
     *  in the same transaction (single RPC, single worker, single
     *  transaction). */
    /** Resolved anchor that ``createNewField`` will use right now —
     *  exposed as a getter so the sidebar hint can preview it. */
    get plannedAnchor() {
        return (
            (this.state.selectedField &&
                this.state.inViewFields.includes(this.state.selectedField) &&
                this.state.selectedField) ||
            this.state.groupFieldNames[this.state.groupFieldNames.length - 1] ||
            this.state.inViewFields[this.state.inViewFields.length - 1] ||
            ""
        );
    }

    async createNewField() {
        const label = (this.state.newFieldLabel || "").trim();
        if (!label) {
            this.notification.add(_t("Enter a label first."), { type: "warning" });
            return;
        }
        if (!this.state.currentModelId) {
            this.notification.add(_t("Model id not detected."), { type: "danger" });
            return;
        }
        if (!this.state.currentViewId) {
            this.notification.add(_t("View id not detected."), { type: "danger" });
            return;
        }
        // Auto-derive a snake_case x_studio_ name from the label.
        const tech =
            "x_studio_" +
            label
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, "_")
                .replace(/^_+|_+$/g, "")
                .substring(0, 50);
        // Placement preference, in order:
        //   1. The currently-selected field (user clicked it then Create).
        //   2. The last <field> *inside a <group>* — these render as
        //      properly-labelled inputs. Skipping non-group fields
        //      avoids "after properties" which lands the new field in a
        //      flex container that hides it.
        //   3. The last <field> anywhere as a last resort.
        const anchor =
            (this.state.selectedField &&
                this.state.inViewFields.includes(this.state.selectedField) &&
                this.state.selectedField) ||
            this.state.groupFieldNames[this.state.groupFieldNames.length - 1] ||
            this.state.inViewFields[this.state.inViewFields.length - 1] ||
            "";
        this.state.saving = true;
        try {
            const result = await this.orm.call(
                "studio.custom.field",
                "studio_create_and_place",
                [
                    {
                        name: label,
                        technical_name: tech,
                        model_id: this.state.currentModelId,
                        field_type: this.state.newFieldType,
                    },
                    this.state.currentViewId,
                    anchor,
                    "after",
                ],
            );
            if (result.customization_state === "applied") {
                this.notification.add(
                    _t("Field %s created and placed — reloading view…", result.field_name),
                    { type: "success" },
                );
                await this._reloadAction();
                await this._initialize();
            } else {
                this.notification.add(
                    _t(
                        "Field created but placement failed: %s",
                        result.last_error || "unknown",
                    ),
                    { type: "danger", sticky: true },
                );
            }
            this.state.newFieldLabel = "";
        } catch (e) {
            this.notification.add(
                _t("Create failed: %s", e.message || String(e)),
                { type: "danger", sticky: true },
            );
        } finally {
            this.state.saving = false;
        }
    }

    // ---------- Shared helpers ----------

    /** Persist a batch of operations onto the current view's
     *  customization (creating one if needed) and then re-apply.
     *
     *  If apply fails, automatically rolls back the freshly-added ops
     *  so a broken edit never sticks in the DB — this is what kept
     *  triggering the same error on every subsequent attempt before.
     */
    async _appendOpsAndApply(ops) {
        if (!ops || !ops.length || !this.state.currentViewId) return;
        this.state.saving = true;
        try {
            const existing = await this.orm.searchRead(
                "studio.view.customization",
                [["target_view_id", "=", this.state.currentViewId]],
                ["id"],
                { limit: 1, order: "id desc" },
            );

            // Snapshot the existing operation ids so we can identify
            // which ones we just added and remove only those on rollback.
            let custId;
            let preExistingOpIds = [];
            const opTuples = ops.map((o) => [0, 0, o]);
            if (existing.length) {
                custId = existing[0].id;
                const before = await this.orm.searchRead(
                    "studio.view.operation",
                    [["customization_id", "=", custId]],
                    ["id"],
                );
                preExistingOpIds = before.map((o) => o.id);
                await this.orm.write("studio.view.customization", [custId], {
                    operation_ids: opTuples,
                });
            } else {
                custId = await this.orm.create("studio.view.customization", [
                    {
                        name: `Studio overlay edits — view ${this.state.currentViewId}`,
                        target_view_id: this.state.currentViewId,
                        operation_ids: opTuples,
                    },
                ]);
                custId = Array.isArray(custId) ? custId[0] : custId;
            }

            await this.orm.call("studio.view.customization", "action_apply", [
                [custId],
            ]);
            const [persisted] = await this.orm.read(
                "studio.view.customization",
                [custId],
                ["state", "last_error"],
            );

            if (persisted.state === "applied") {
                this.notification.add(_t("Applied — reloading view…"), {
                    type: "success",
                });
                await this._reloadAction();
                await this._initialize();
                if (this.state.selectedField) {
                    await this._loadFieldProperties(this.state.selectedField);
                }
            } else {
                // Roll back: drop the ops we just added so the next
                // apply runs against the previously-known-good op list.
                const after = await this.orm.searchRead(
                    "studio.view.operation",
                    [["customization_id", "=", custId]],
                    ["id"],
                );
                const newOpIds = after
                    .map((o) => o.id)
                    .filter((id) => !preExistingOpIds.includes(id));
                if (newOpIds.length) {
                    await this.orm.unlink("studio.view.operation", newOpIds);
                }
                // Re-apply on the pre-existing op set to clear the
                // customization's error state.
                if (preExistingOpIds.length) {
                    try {
                        await this.orm.call(
                            "studio.view.customization",
                            "action_apply",
                            [[custId]],
                        );
                    } catch (e) {
                        // ignore — even the prior state was bad.
                    }
                }
                this.notification.add(
                    _t(
                        "Op rejected: %s (your change was rolled back)",
                        persisted.last_error || "unknown",
                    ),
                    { type: "danger", sticky: true },
                );
            }
        } catch (e) {
            this.notification.add(
                _t("Apply failed: %s", e.message || String(e)),
                { type: "danger", sticky: true },
            );
        } finally {
            this.state.saving = false;
        }
    }

    /** Surface (and let the user prune) the operations currently stored
     *  on this view's customization. Read-only listing — destructive
     *  cleanup goes through deleteOp(). */
    async loadOperations() {
        if (!this.state.currentViewId) return;
        const existing = await this.orm.searchRead(
            "studio.view.customization",
            [["target_view_id", "=", this.state.currentViewId]],
            ["id"],
            { limit: 1, order: "id desc" },
        );
        if (!existing.length) {
            this.state.operations = [];
            this.state.customizationId = null;
            return;
        }
        this.state.customizationId = existing[0].id;
        const ops = await this.orm.searchRead(
            "studio.view.operation",
            [["customization_id", "=", existing[0].id]],
            ["id", "sequence", "op_type", "field_name", "anchor_field", "position", "attr_name", "attr_value"],
            { order: "sequence, id" },
        );
        this.state.operations = ops;
    }

    /** Emergency wipe: drop every studio.view.customization op for this
     *  view and deactivate any materialised inheritance. The user
     *  triggers this from the Ops tab when accumulated state has gotten
     *  out of sync. */
    async resetView() {
        if (!this.state.currentViewId) return;
        const ok = window.confirm(
            _t("Reset ALL Studio edits on this view? This deactivates every inheritance and drops every queued operation."),
        );
        if (!ok) return;
        this.state.saving = true;
        try {
            await this.orm.call("studio.custom.field", "studio_reset_view", [
                this.state.currentViewId,
            ]);
            this.notification.add(_t("Studio edits reset — reloading view…"), {
                type: "success",
            });
            await this._reloadAction();
            this.state.selectedField = null;
            this.state.tab = "add";
            await this._initialize();
        } catch (e) {
            this.notification.add(_t("Reset failed: %s", e.message || String(e)), {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.saving = false;
        }
    }

    async deleteOp(opId) {
        try {
            await this.orm.unlink("studio.view.operation", [opId]);
            if (this.state.customizationId) {
                await this.orm.call(
                    "studio.view.customization",
                    "action_apply",
                    [[this.state.customizationId]],
                );
            }
            await this._reloadAction();
            await this._initialize();
            await this.loadOperations();
        } catch (e) {
            this.notification.add(_t("Delete op failed: %s", e.message || String(e)), {
                type: "danger",
            });
        }
    }

    async _reloadAction() {
        // Bust the on-disk view cache. Odoo only auto-invalidates it when
        // JS calls write/unlink on ir.ui.view directly; our edits go via
        // ``studio.view.customization.action_apply`` (server-side python
        // writes the inheritance), so we need to clear the cache by hand.
        rpcBus.trigger("CLEAR-CACHES", "get_views");
        const ctrl = this.action.currentController;
        if (!ctrl || !ctrl.action) return;
        const a = ctrl.action;
        // Re-doing the *same* action object instance does NOT force
        // action_service to refetch views — it keeps the resolved
        // _views/_originalAction cache stamped on the action. To get
        // a fresh load_views call (and therefore a fresh arch after
        // server-side inheritance writes) we have to either pass the
        // action id (server re-resolves) or strip the cached fields.
        const opts = {
            clearBreadcrumbs: false,
            additionalContext: (ctrl.props && ctrl.props.context) || {},
        };
        if (ctrl.view && ctrl.view.type) {
            opts.viewType = ctrl.view.type;
        }
        if (ctrl.props && ctrl.props.resId) {
            opts.resId = ctrl.props.resId;
        }
        if (a.id) {
            await this.action.doAction(a.id, opts);
        } else {
            // Inline / window actions without an id: shallow-clone and
            // drop the cached resolved-views array so doAction reloads.
            const fresh = Object.assign({}, a);
            delete fresh._views;
            delete fresh._originalAction;
            await this.action.doAction(fresh, opts);
        }
    }

    async hideSelectedField() {
        if (!this.state.selectedField) return;
        if (!this.state.currentViewId) return;
        this.state.saving = true;
        try {
            const existing = await this.orm.searchRead(
                "studio.view.customization",
                [["target_view_id", "=", this.state.currentViewId]],
                ["id"],
                { limit: 1, order: "id desc" },
            );
            const op = {
                op_type: "hide_field",
                field_name: this.state.selectedField,
                anchor_field: "",
                position: "after",
            };
            let custId;
            if (existing.length) {
                custId = existing[0].id;
                await this.orm.write("studio.view.customization", [custId], {
                    operation_ids: [[0, 0, op]],
                });
            } else {
                custId = await this.orm.create("studio.view.customization", [
                    {
                        name: `Studio overlay edits — view ${this.state.currentViewId}`,
                        target_view_id: this.state.currentViewId,
                        operation_ids: [[0, 0, op]],
                    },
                ]);
                custId = Array.isArray(custId) ? custId[0] : custId;
            }
            await this.orm.call("studio.view.customization", "action_apply", [
                [custId],
            ]);
            this.notification.add(
                _t("Hid %s — reloading view…", this.state.selectedField),
                { type: "success" },
            );
            this.state.selectedField = null;
            // Use the same forced-refetch path as _appendOpsAndApply,
            // otherwise the action service serves the cached arch and
            // the hidden field stays visible until a hard refresh.
            await this._reloadAction();
            await this._initialize();
        } catch (e) {
            this.notification.add(_t("Hide failed: %s", e.message || String(e)), {
                type: "danger",
                sticky: true,
            });
        } finally {
            this.state.saving = false;
        }
    }

    close() {
        this.overlayService.deactivate();
    }

    /** Filtered subset of availableFields shown in the sidebar. */
    get filteredFields() {
        const q = (this.state.filter || "").toLowerCase().trim();
        if (!q) return this.state.availableFields;
        return this.state.availableFields.filter(
            (f) =>
                f.name.toLowerCase().includes(q) ||
                (f.field_description || "").toLowerCase().includes(q),
        );
    }
}

registry.category("main_components").add("custom_studio_lite.studio_overlay", {
    Component: StudioOverlay,
});

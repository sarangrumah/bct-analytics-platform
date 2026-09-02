/** @odoo-module **/

import { Component, useState, useRef } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Spotlight-style search field embedded in the Home Console header.
 *
 * On focus or query input, it opens Odoo's built-in command palette
 * (Ctrl+K), reusing its providers (menus, records, actions). We
 * intentionally do not reimplement search logic — every provider
 * registered in `command_provider` registry is available for free.
 */
export class SpotlightSearch extends Component {
    static template = "custom_home_console.SpotlightSearch";
    static props = {
        apps: { type: Array, optional: true },
        onAppClick: { type: Function, optional: true },
    };
    static defaultProps = {
        apps: [],
        onAppClick: () => {},
    };

    setup() {
        this.command = useService("command");
        this.inputRef = useRef("input");
        this.state = useState({ query: "" });
    }

    _openPalette(searchValue) {
        // command service exposes openMainPalette (Odoo 17+); guarded
        // because the API name has shifted in past versions.
        if (typeof this.command.openMainPalette === "function") {
            this.command.openMainPalette({ searchValue: searchValue || "" });
        } else if (typeof this.command.openPalette === "function") {
            this.command.openPalette({ searchValue: searchValue || "" });
        }
    }

    // Click — not focus — triggers the palette. Using focus caused
    // a loop: the palette closing (Escape / click-outside) returned
    // focus to our input, which re-fired onFocus and reopened it.
    onClick(ev) {
        this._openPalette("");
        ev.currentTarget.blur();
    }

    onInput(ev) {
        const q = ev.target.value;
        this._openPalette(q);
        ev.target.value = "";
        ev.target.blur();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this._openPalette(ev.target.value || "");
            ev.target.blur();
        }
    }

    _appMatches(app) {
        const q = this.state.query.trim().toLowerCase();
        if (!q) {
            return false;
        }
        return (app.name || "").toLowerCase().includes(q);
    }

    get inlineMatches() {
        if (!this.state.query) {
            return [];
        }
        return this.props.apps.filter((a) => this._appMatches(a)).slice(0, 5);
    }
}

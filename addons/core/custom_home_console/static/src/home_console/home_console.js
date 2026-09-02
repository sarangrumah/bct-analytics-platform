/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { SpotlightSearch } from "./spotlight_search";

// --- Icon overrides ---------------------------------------------------
// Some module slugs don't match a local icon filename (the wrapper
// custom_<x> has its own naming, or the upstream module ships under a
// different name). Keyed by slug = xmlid module prefix after stripping
// any leading "custom_" — verified against ir_ui_menu in erp_dev.
const APP_ICON_OVERRIDES = {
    barcode: "stock_barcode",            // custom_barcode
    quality_full: "quality_control",     // custom_quality_full
    attendance: "hr_attendance",         // custom_attendance
    marketing_card: "industry_marketing_agency",
    sms_id: "sms",                       // custom_sms_id
    ai_features: "ai",                   // custom_ai_features
    whatsapp: "whatsapp",                // custom_whatsapp -> whatsapp.png
    appointments: "appointment",         // custom_appointments
    approval_engine: "approvals",        // custom_approval_engine
    data_cleaning: "data_recycle",       // custom_data_cleaning
    iot_bridge: "iot",                   // custom_iot_bridge
    studio_lite: "web_studio",           // custom_studio_lite
    core: "industry_custom_furniture",   // custom_core -> "Custom"
    super_admin: "industry_headhunter",  // custom_super_admin
    ecommerce: "website_sale",           // custom_ecommerce
    field_service: "website_membership", // custom_field_service
    queue_job: "website_partner",        // queue_job (no custom_ prefix)
    rental: "sale_renting",              // custom_rental
};

// --- Synthetic apps ---------------------------------------------------
// Cards that have no backing ir.ui.menu but still need to show in the
// Home Console. Currently just "Settings" — exposed as a card pointing
// at the modern General Settings action.
const SYNTHETIC_APPS = [
    {
        id: "synthetic:settings",
        name: "Settings",
        xmlid: "base_setup.action_general_configuration",
        _iconFile: "settings",
        _group: "admin",
        _isSyntheticAction: true,
    },
];

// Menu xmlids that should never appear as a card.
//   - Home: redundant since the navbar apps icon already opens this.
//   - base.menu_administration: legacy "Settings" tree (Apps, Users,
//     Companies, Translations) — overlaps with our synthetic Settings
//     card which opens the modern General Settings action. Hiding the
//     legacy one keeps a single, well-iconed Settings entry.
const HIDDEN_APP_XMLIDS = new Set([
    "custom_home_console.menu_home_console_root",
    "base.menu_administration",
]);

// --- Grouping ---------------------------------------------------------
const GROUP_LABELS = {
    sales: "Sales & CRM",
    finance: "Finance",
    operations: "Operations",
    people: "People",
    marketing: "Marketing & Web",
    services: "Services",
    productivity: "Productivity",
    admin: "Administration",
    other: "Other",
};
const GROUP_ORDER = [
    "sales", "finance", "operations", "people",
    "marketing", "services", "productivity",
    "admin", "other",
];

// Explicit per-module group assignment. Key = upstream module slug
// (after stripping any leading custom_). Anything missing here falls
// through to _heuristicGroup() below.
const MODULE_TO_GROUP = {
    // Sales & CRM
    crm: "sales", sale: "sales", sale_management: "sales",
    contacts: "sales", sale_subscription: "sales",
    sale_renting: "sales",
    point_of_sale: "sales", pos_restaurant: "sales",

    // Finance
    account: "finance", account_accountant: "finance",
    account_asset: "finance", account_reports: "finance",
    payment: "finance", hr_expense: "finance",
    coretax: "finance", coretax_bupot: "finance",
    coretax_pajakku: "finance", pph_witholding: "finance",
    bast: "finance", tax_id: "finance",

    // Operations / Supply chain
    stock: "operations", inventory: "operations",
    purchase: "operations", mrp: "operations", mrp_plm: "operations",
    repair: "operations", quality_control: "operations",
    maintenance: "operations", fleet: "operations",
    stock_barcode: "operations",
    wms_cycle_count: "operations", wms_putaway: "operations",
    wms_to_engine: "operations",

    // People (HR)
    hr: "people", hr_attendance: "people", hr_holidays: "people",
    hr_payroll: "people", hr_recruitment: "people",
    hr_appraisal: "people", hr_referral: "people",
    hr_timesheet: "people", hr_skills: "people",
    lunch: "people", attendance: "people",

    // Marketing & Web
    website: "marketing", website_sale: "marketing",
    website_slides: "marketing", website_event: "marketing",
    website_forum: "marketing", mass_mailing: "marketing",
    mass_mailing_sms: "marketing", marketing_automation: "marketing",
    social: "marketing", survey: "marketing",
    im_livechat: "marketing", whatsapp: "marketing",

    // Services
    project: "services", planning: "services", helpdesk: "services",
    industry_fsm: "services", appointment: "services",
    frontdesk: "services", calendar: "services",

    // Productivity
    discuss: "productivity", mail: "productivity",
    knowledge: "productivity", project_todo: "productivity",
    documents: "productivity", spreadsheet: "productivity",
    sign: "productivity", board: "productivity",
    approvals: "productivity", web_studio: "productivity",
    voip: "productivity", iot: "productivity",
    ai_features: "productivity", ai_bridge: "productivity",
    data_cleaning: "productivity",

    // Administration / Platform
    base: "admin", web: "admin", base_setup: "admin",
    hub_console: "admin", super_admin: "admin",
    tenant_infra: "admin", ops_monitor: "admin",
    onboarding_journey: "admin", dev_cycle: "admin",
    core: "admin", home_console: "admin",
    adapter_framework: "admin", hht_bridge: "admin",
    brd_analyzer: "admin",
    pdp_core: "admin", pdp_audit: "admin",
    pdp_consent: "admin", pdp_dsar: "admin",
    pdp_masking: "admin", pdp_retention: "admin",
    esg: "admin",
};

// Last-resort heuristic when MODULE_TO_GROUP misses (e.g., third-party
// module installed later). Errs on the side of "other".
function _heuristicGroup(slug) {
    const s = (slug || "").toLowerCase();
    if (/(hr|payroll|attendance|leave|recruit|employee)/.test(s)) return "people";
    if (/(account|invoic|tax|finance|expense|payment)/.test(s)) return "finance";
    if (/(stock|inventory|purchase|mrp|manufactur|repair|quality|maintenance|fleet|barcode)/.test(s)) return "operations";
    if (/(crm|sale|contact|subscription)/.test(s)) return "sales";
    if (/(website|mail|social|sms|marketing|survey|event|forum|livechat|whatsapp)/.test(s)) return "marketing";
    if (/(project|plan|helpdesk|appointment|fsm|frontdesk|calendar)/.test(s)) return "services";
    if (/(discuss|knowledge|todo|document|spreadsheet|sign|dashboard|approval|studio|ai|iot|voip)/.test(s)) return "productivity";
    if (/(admin|setting|hub|tenant|core|pdp|ops|onboard|dev|coretax|esg|bast)/.test(s)) return "admin";
    return "other";
}

// Mirror of tools/sync_module_icons.sh MAPPING — upstream Odoo module
// slug → custom_<x> wrapper folder where we synced the official icon.
// Used as a last-resort URL fallback when /<upstream>/static/description/
// icon.png 404s (CE doesn't ship icons for EE-only apps like helpdesk,
// knowledge, planning, etc.).
const UPSTREAM_TO_CUSTOM = {
    account_asset: "custom_accounting_asset",
    account_accountant: "custom_accounting_full",
    account_reports: "custom_accounting_reports",
    appointment: "custom_appointments",
    approvals: "custom_approval_engine",
    hr_attendance: "custom_attendance",
    stock_barcode: "custom_barcode",
    crm: "custom_crm",
    board: "custom_dashboards",
    data_cleaning: "custom_data_cleaning",
    documents: "custom_documents",
    website_sale: "custom_ecommerce",
    website_slides: "custom_elearning",
    mass_mailing: "custom_email_marketing",
    website_event: "custom_events",
    hr_expense: "custom_expenses",
    industry_fsm: "custom_field_service",
    fleet: "custom_fleet_id",
    website_forum: "custom_forum",
    frontdesk: "custom_frontdesk",
    helpdesk: "custom_helpdesk",
    hr_appraisal: "custom_hr_appraisal",
    hr_holidays: "custom_hr_leave_id",
    hr_payroll: "custom_hr_payroll_id",
    hr_referral: "custom_hr_referral",
    iot: "custom_iot_bridge",
    knowledge: "custom_knowledge",
    im_livechat: "custom_livechat",
    lunch: "custom_lunch",
    maintenance: "custom_maintenance",
    marketing_automation: "custom_marketing_automation",
    mrp_plm: "custom_mrp_plm",
    payment: "custom_payment_id",
    planning: "custom_planning",
    point_of_sale: "custom_pos_id",
    quality_control: "custom_quality_full",
    hr_recruitment: "custom_recruitment_id",
    sale_renting: "custom_rental",
    repair: "custom_repairs",
    sign: "custom_sign",
    mass_mailing_sms: "custom_sms_id",
    social: "custom_social",
    spreadsheet: "custom_spreadsheet",
    web_studio: "custom_studio_lite",
    sale_subscription: "custom_subscription",
    survey: "custom_survey",
    hr_timesheet: "custom_timesheet",
    project_todo: "custom_todo",
    voip: "custom_voip",
    whatsapp: "custom_whatsapp",
};

/**
 * Custom Home Console.
 *
 * Replaces Odoo's default post-login landing with:
 *  - Spotlight-style search (reuses command palette).
 *  - App cards grouped by ir.module.category.home_console_group.
 *  - Pinned shortcuts (server) + recent apps (localStorage).
 *  - Per-tenant branding (accent + logo + announcement banner).
 *  - Lightweight onboarding checklist.
 *
 * The component is registered as the client action
 * ``custom_home_console.home``; the post-install hook makes it the
 * default landing for every user without an explicit action_id.
 */
export class HomeConsole extends Component {
    static template = "custom_home_console.HomeConsole";
    static components = { SpotlightSearch };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.menu = useService("menu");
        this.ui = useService("ui");
        this.recent = useService("home_console_recent");

        this.state = useState({
            loading: true,
            bootstrap: null,
            dismissedAnnouncement: this._isAnnouncementDismissed(),
            apps: [],
            grouped: [],
        });

        onWillStart(async () => {
            this._loadApps();
            this.state.bootstrap = await this.orm.call(
                "res.users", "home_console_bootstrap", []);
            await this._enrichGroups();
            this.state.loading = false;
        });
    }

    // --- App loading -----------------------------------------------------

    _loadApps() {
        // Keep the full menu object (including appID) so
        // menuService.selectMenu(app) -> setCurrentMenu(app) can read
        // app.appID and update the navbar's current-app context.
        // Filter out menus we don't want as cards (e.g. "Home" itself).
        const real = (this.menu.getApps() || [])
            .filter((app) => !HIDDEN_APP_XMLIDS.has(app.xmlid))
            .map((app) => ({ ...app }));
        this.state.apps = [...real, ...SYNTHETIC_APPS];
    }

    async _enrichGroups() {
        // Per-app grouping rules. Bucket lookup by upstream module slug
        // (strip leading custom_ wrapper). Synthetic apps carry an
        // explicit _group. Anything not matched falls into "other".
        const buckets = {};
        for (const app of this.state.apps) {
            let grp;
            if (app._group) {
                grp = app._group;
            } else {
                const raw = (app.xmlid || "").split(".")[0];
                const slug = raw.replace(/^custom_/, "");
                grp = MODULE_TO_GROUP[slug] || _heuristicGroup(slug);
            }
            (buckets[grp] = buckets[grp] || []).push(app);
        }
        this.state.grouped = GROUP_ORDER
            .filter((k) => buckets[k] && buckets[k].length)
            .map((k) => ({
                key: k,
                label: GROUP_LABELS[k],
                apps: buckets[k].sort((a, b) => a.name.localeCompare(b.name)),
            }));
    }

    // --- Branding helpers ------------------------------------------------

    get accent() {
        return (this.state.bootstrap && this.state.bootstrap.company.accent)
            || "#714B67";
    }

    get logoUrl() {
        const co = this.state.bootstrap && this.state.bootstrap.company;
        if (!co) {
            return "/web/static/img/logo.png";
        }
        if (co.has_home_logo) {
            return `/web/image?model=res.company&id=${co.id}&field=brand_logo_home`;
        }
        if (co.has_logo) {
            return `/web/image?model=res.company&id=${co.id}&field=logo`;
        }
        return "/web/static/img/logo.png";
    }

    get headerStyle() {
        return `--hc-accent: ${this.accent};`;
    }

    appIconSrc(app) {
        // Priority order:
        //   1. app._iconFile (synthetic apps carry an explicit filename)
        //   2. APP_ICON_OVERRIDES[slug] — explicit mapping for wrappers
        //      whose slug doesn't match the icon filename
        //   3. /custom_home_console/static/icons/<slug>.png — direct
        //      match in the 303-icon local library
        //   4. webIconData base64 from the menu
        //   5. fa-cube fallback (via onIconError)
        if (app._iconFile) {
            return `/custom_home_console/static/icons/${app._iconFile}.png`;
        }
        const rawModule = (app.xmlid || "").split(".")[0];
        const slug = rawModule.replace(/^custom_/, "");
        const override = APP_ICON_OVERRIDES[slug];
        if (override) {
            return `/custom_home_console/static/icons/${override}.png`;
        }
        if (slug) {
            return `/custom_home_console/static/icons/${slug}.png`;
        }
        if (app.webIconData) {
            return app.webIconData;
        }
        return null;
    }

    appIconFallback(app) {
        // Chained onerror fallback: if the local icons folder doesn't
        // have a match, try webIconData, then /<module>/static/...,
        // then /<custom_x>/static/...
        if (app.webIconData) {
            return app.webIconData;
        }
        const rawModule = (app.xmlid || "").split(".")[0];
        if (!rawModule) {
            return "";
        }
        return `/${rawModule}/static/description/icon.png`;
    }

    onIconError(ev) {
        const img = ev.target;
        const fallback = img.dataset.fallback;
        if (fallback && img.src !== fallback && !img.src.endsWith(fallback)) {
            // Try the fallback URL once.
            img.dataset.fallback = "";
            img.src = fallback;
            return;
        }
        // Out of options — hide the img and reveal the fa-cube sibling.
        img.style.display = "none";
        const next = img.nextElementSibling;
        if (next) {
            next.style.display = "inline-flex";
        }
    }

    get greeting() {
        const h = new Date().getHours();
        if (h < 11) return "Selamat pagi";
        if (h < 15) return "Selamat siang";
        if (h < 19) return "Selamat sore";
        return "Selamat malam";
    }

    get isCompact() {
        return (this.state.bootstrap
            && this.state.bootstrap.user.density === "compact")
            || this.ui.isSmall;
    }

    // --- Announcement dismissal -----------------------------------------

    _announcementKey() {
        const co = this.state.bootstrap && this.state.bootstrap.company;
        return `custom_home_console.ann_dismissed.${co ? co.id : 0}`;
    }

    _isAnnouncementDismissed() {
        try {
            const until = parseInt(
                localStorage.getItem(`custom_home_console.ann_until`) || "0",
                10,
            );
            return until > Date.now();
        } catch (e) {
            return false;
        }
    }

    onDismissAnnouncement() {
        try {
            const ttl = 24 * 60 * 60 * 1000;
            localStorage.setItem(
                `custom_home_console.ann_until`,
                String(Date.now() + ttl),
            );
        } catch (e) {
            // ignore
        }
        this.state.dismissedAnnouncement = true;
    }

    // --- Click handlers --------------------------------------------------

    onClickApp(app) {
        this.recent.push(app);
        // Synthetic apps (e.g. Settings) point to an action xmlid, not
        // a menu — drive doAction directly. We still have to push the
        // real Settings root menu (base.menu_administration) into
        // menuService.currentApp, otherwise the NavBar has no current
        // app and the secondary menu strip (Users & Companies,
        // Technical, Translations, …) renders empty.
        if (app._isSyntheticAction) {
            if (app.id === "synthetic:settings") {
                const adminMenu = (this.menu.getApps() || [])
                    .find((a) => a.xmlid === "base.menu_administration");
                if (adminMenu) {
                    this.menu.setCurrentMenu(adminMenu);
                }
            }
            this.action.doAction(app.xmlid, { clearBreadcrumbs: true });
            return;
        }
        // For real menus, use selectMenu (not doAction) so the navbar's
        // current-app context is updated — without it submenus don't
        // appear and the top bar stays stuck on the previous app.
        if (app.id) {
            this.menu.selectMenu(app);
        } else if (app.actionID) {
            this.action.doAction(app.actionID, { clearBreadcrumbs: true });
        }
    }

    onClickShortcut(shortcut) {
        if (shortcut.id) {
            this.action.doAction(shortcut.id, { clearBreadcrumbs: true });
        }
    }

    onClickActivity(act) {
        if (!act.res_model) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: act.res_model,
            res_id: act.res_id || false,
            views: [[false, "form"]],
        });
    }

    onQuickCreate(model) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            views: [[false, "form"]],
            target: "current",
        });
    }

    get recentApps() {
        return this.recent.list();
    }

    get pinned() {
        return (this.state.bootstrap && this.state.bootstrap.pinned) || [];
    }

    get activities() {
        return (this.state.bootstrap
            && this.state.bootstrap.recent_activities) || [];
    }

    get announcement() {
        const co = this.state.bootstrap && this.state.bootstrap.company;
        if (!co || !co.announcement_html || this.state.dismissedAnnouncement) {
            return null;
        }
        return co.announcement_html;
    }

    get checklist() {
        return (this.state.bootstrap && this.state.bootstrap.checklist) || [];
    }

    get checklistProgress() {
        const items = this.checklist;
        if (!items.length) {
            return 100;
        }
        const done = items.filter((i) => i.done).length;
        return Math.round((done / items.length) * 100);
    }
}

registry.category("actions").add("custom_home_console.home", HomeConsole);

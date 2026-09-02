/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

/**
 * Add an openHomeConsole() method on NavBar.
 *
 * The navbar template (navbar_apps_patch.xml) calls this when the apps
 * icon is clicked. Going through menuService.selectMenu — not
 * actionService.doAction directly — is what updates
 * menuService.currentApp to our "Home" menu. Without that, the navbar
 * keeps showing the previously-active app's brand and submenus when
 * the user returns to the Home Console.
 */
const HOME_MENU_XMLID = "custom_home_console.menu_home_console_root";
const HOME_ACTION_XMLID = "custom_home_console.action_home_console";

patch(NavBar.prototype, {
    openHomeConsole() {
        const apps = this.menuService.getApps() || [];
        const home = apps.find((a) => a.xmlid === HOME_MENU_XMLID);
        if (home && home.actionID) {
            this.menuService.selectMenu(home);
        } else {
            // Fallback if the Home menu was uninstalled or its xmlid
            // changed — at least the action still loads.
            this.actionService.doAction(HOME_ACTION_XMLID, {
                clearBreadcrumbs: true,
            });
        }
    },
});

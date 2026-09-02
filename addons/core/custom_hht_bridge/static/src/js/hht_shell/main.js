/** @odoo-module **/
// License: LGPL-3
import { whenReady } from "@odoo/owl";
import { mountComponent } from "@web/env";

import { HhtShell } from "./hht_shell";

(async function startHhtShell() {
    await whenReady();
    await mountComponent(HhtShell, document.getElementById("hht-app"), {
        name: "Hub HHT",
    });
})();

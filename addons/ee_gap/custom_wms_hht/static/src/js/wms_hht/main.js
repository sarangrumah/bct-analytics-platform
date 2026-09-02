/** @odoo-module **/
// License: LGPL-3
import { whenReady } from "@odoo/owl";
import { mountComponent } from "@web/env";

import { WmsHhtShell } from "./wms_shell";

(async function startWmsHht() {
    await whenReady();
    await mountComponent(WmsHhtShell, document.getElementById("hht-app"), {
        name: "WMS Handheld",
    });
})();

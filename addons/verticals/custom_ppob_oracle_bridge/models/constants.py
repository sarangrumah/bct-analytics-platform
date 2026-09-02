# -*- coding: utf-8 -*-
"""Oracle Bridge constants -- status mapping and shared keys.

Maps ``MSG016T.STATUS_USSD_2_PROVIDER`` -> Odoo transaction state.
"""

# Map raw Oracle status code -> Odoo state token.
# Unknown codes log a warning and keep the transaction in_progress.
ORACLE_STATUS_MAP = {
    "P": "in_progress",  # Pending -- newly inserted, not yet sent to provider
    "S": "in_progress",  # Sent -- awaiting response
    "D": "success",  # Done -- provider confirmed success
    "OK": "success",  # Alternative success marker (legacy)
    "C": "failed",  # Cancelled / Failed
    None: "in_progress",  # NULL during initial INSERT before status update
}

# Terminal status values -- cron status sync stops polling once reached.
ORACLE_STATUS_TERMINAL = {"D", "OK", "C"}

# ir.config_parameter keys used by this module.
PARAM_INBOUND_CURSOR = "custom_ppob_oracle_bridge.last_seen_msg016t_id"
PARAM_BACKFILL_RANGE_LOG = "custom_ppob_oracle_bridge.last_backfill_range"

# -*- coding: utf-8 -*-
"""Shared constants for the POS mirror bridge."""

# Per-feed suffix appended to pos_trx_ref to form the idempotent external ref.
FEED_POS = "pos"
FEED_H2H = "h2h"
FEED_SUFFIX = {FEED_POS: ":pos", FEED_H2H: ":h2h"}

# Terminal statuses accepted from either feed (case-insensitive on ingest).
# Odoo ingests TERMINAL events only; anything else is rejected as non-final.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_SUCCESS, STATUS_FAILED)

# Wallet move types whitelisted to write mirror-source wallets.
MIRROR_MOVE_TYPES = (
    "pos_sale",
    "pos_topup",
    "pos_refund",
    "pos_sync",
)

# ir.config_parameter keys.
PARAM_POS_ONLY_SLA_MIN = "custom_ppob_pos_bridge.pos_only_sla_minutes"
PARAM_DEPOSIT_SNAPSHOT = "custom_ppob_pos_bridge.last_deposit_snapshot"

# Default threshold (minutes) after which a pos_only join row is flagged as a
# potential feed drop / unfulfilled sale (reconciliation control, not blocking).
DEFAULT_POS_ONLY_SLA_MIN = 30

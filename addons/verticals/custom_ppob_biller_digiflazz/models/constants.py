# -*- coding: utf-8 -*-
"""Digiflazz protocol constants.

Everything here is transcribed from the public docs at
https://developer.digiflazz.com/api/ and has NOT been confirmed against a live
server. The rc mapping in particular is the part most likely to be wrong: real
providers emit codes their documentation never mentions.
"""

DEFAULT_BASE_URL = "https://api.digiflazz.com/v1"

# Endpoints (all POST, JSON body, response wrapped in a top-level "data" key).
PATH_TRANSACTION = "/transaction"
PATH_CEK_SALDO = "/cek-saldo"
PATH_PRICE_LIST = "/price-list"

# Signature suffixes. Transactions sign with the ref_id itself rather than a
# constant, so they have no entry here.
SIGN_SUFFIX_DEPOSIT = "depo"  # NOT "deposit" -- the docs are explicit
SIGN_SUFFIX_PRICELIST = "pricelist"

# commands values on /transaction. Prepaid topup sends NO commands key at all.
CMD_INQUIRY_POSTPAID = "inq-pasca"
CMD_PAY_POSTPAID = "pay-pasca"
CMD_STATUS_POSTPAID = "status-pasca"

# status values in the response payload.
STATUS_SUCCESS = "Sukses"
STATUS_PENDING = "Pending"
STATUS_FAILED = "Gagal"

# Digiflazz "diproses secara sinkron", yet prepaid topups routinely answer
# Pending and settle later, so pending must be a first-class outcome rather than
# an edge case.
TERMINAL_STATUSES = (STATUS_SUCCESS, STATUS_FAILED)

# Documented operational hazards, enforced as guards in the adapter.
DEFAULT_STATUS_MIN_AGE_S = 60  # "<1 menit ... race condition atau duplikasi"
DEFAULT_STATUS_MAX_AGE_DAYS = 90  # beyond retention a re-send BOOKS A NEW SALE

DEFAULT_TIMEOUT_S = 30

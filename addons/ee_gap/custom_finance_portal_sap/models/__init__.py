# -*- coding: utf-8 -*-
# Import the adapter first so @register_adapter runs before the config selection
# field is evaluated.
from . import finance_sap_adapter
from . import finance_sync_log
from . import finance_document_sap

# -*- coding: utf-8 -*-
# The adapter is imported first so @register_adapter runs before
# custom.adapter.config builds its adapter_type selection.
from . import wms_host_adapter
from . import wms_integration_mapping
from . import wms_integration_event
from . import stock_picking

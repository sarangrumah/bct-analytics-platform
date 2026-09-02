# -*- coding: utf-8 -*-
from . import pdp_audit_log
from . import pdp_audited_mixin
from . import res_partner
from . import res_users

# hr.employee is wired only when the `hr` addon is installed in the same db.
# We attempt the import; if `hr` is unavailable in the python path the file
# itself will be a no-op (model registry won't have hr.employee and Odoo
# will simply ignore the unresolved _inherit).

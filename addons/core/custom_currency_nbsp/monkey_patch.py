# -*- coding: utf-8 -*-
"""Strip the exotic whitespace Odoo bakes into currency strings.

``odoo.tools.misc.format_amount`` / ``formatLang`` glue the currency symbol to
the amount with U+00A0 and prefix the minus sign with U+FEFF. Both survive fine
as long as everything downstream agrees the stream is UTF-8; the moment
something decodes them as Latin-1 they surface as ``Â`` / ``ï»¿``. We normalise
them to a plain space at the source.

Why a monkey patch and not an override: these are plain module-level functions,
not ORM methods, so there is nothing to inherit. The consequence is that the
patch is process-global, while installing a module is database-global — hence
the ``_enabled()`` registry gate below, which keeps every other tenant served by
the same worker on the stock behaviour.
"""

import functools
import logging
import sys

from odoo.tools import misc

_logger = logging.getLogger(__name__)

NBSP = " "
ZWNBSP = "﻿"

#: Abstract model declared by this addon; present in the registry of a database
#: only once the addon is installed there.
_MARKER = "nbsp.free.currency"


def normalize(text):
    """Replace NBSP with a plain space and drop the zero-width no-break space."""
    if isinstance(text, str) and (NBSP in text or ZWNBSP in text):
        return text.replace(NBSP, " ").replace(ZWNBSP, "")
    return text


def _enabled(env):
    """True when the calling environment's database has this addon installed."""
    try:
        return _MARKER in env.registry.models
    except Exception:  # pragma: no cover - defensive: env may be a stub in tests
        return False


def _wrap(func):
    """Return ``func`` with its result normalised, when enabled for this env.

    Both wrapped helpers take the environment as their first positional
    argument; everything else is forwarded verbatim so the wrapper survives
    signature changes across Odoo versions.
    """

    @functools.wraps(func)
    def wrapper(env, *args, **kwargs):
        result = func(env, *args, **kwargs)
        return normalize(result) if _enabled(env) else result

    wrapper._nbsp_free = True
    return wrapper


def _rebind(name, original, replacement):
    """Point every ``from odoo.tools import <name>`` binding at the wrapper.

    Modules imported before us hold a direct reference to the original function
    object, which setting the attribute on ``odoo.tools.misc`` would not reach.
    We only rebind names that are still identical to the original, so unrelated
    functions that happen to share a name are left alone.
    """
    rebound = 0
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            if getattr(module, name, None) is original:
                setattr(module, name, replacement)
                rebound += 1
        except Exception:
            continue
    return rebound


def apply():
    if getattr(misc.format_amount, "_nbsp_free", False):
        return  # already patched (module re-imported)

    for name in ("format_amount", "formatLang"):
        original = getattr(misc, name)
        replacement = _wrap(original)
        count = _rebind(name, original, replacement)
        setattr(misc, name, replacement)
        _logger.info("custom_currency_nbsp: patched %s (%d bindings)", name, count)

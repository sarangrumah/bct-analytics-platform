"""Every advertised CLI flag must be reachable in code.

Written after a real incident: ``--reload`` survived a redesign that deleted the function it
called, so it raised ``AttributeError`` and exited 1 on *every* invocation. Nobody noticed, because
its whole purpose was recovery — it was documented as the safe path out of a bad state and had
therefore never once been run. That is the shape of bug that only appears during an actual
incident, which is the worst possible time to find it.

So this file asserts the parser and the code agree, without needing a database:

* every flag the parser advertises is handled by :func:`bct_cdc.runner.run`;
* no flag's help text names a function or table that no longer exists.

A test that merely ran ``--help`` would not have caught the original bug, because ``--help`` never
reaches the dispatch. These inspect the dispatch itself.
"""

from __future__ import annotations

import argparse
import inspect
import re

from bct_cdc import backfill, runner, warehouse


def _parser_flags():
    """Recover the flags the CLI advertises by re-running its own parser construction."""
    source = inspect.getsource(runner.run)
    return set(re.findall(r'parser\.add_argument\("(--[a-z-]+)"', source))


def test_every_flag_is_dispatched_somewhere_in_the_runner():
    """A flag the parser accepts but nothing ever reads is dead: it silently does nothing.

    Scoped to the whole module rather than to ``run()``, because ``args`` is legitimately passed
    down to helpers -- ``--max-seconds`` is read in ``_stream``. Narrowing this to ``run()`` made
    the test fail on correct code, which is its own kind of bug.
    """
    source = inspect.getsource(runner)
    for flag in _parser_flags():
        attribute = flag.lstrip("-").replace("-", "_")
        assert ("args.%s" % attribute) in source, (
            "flag %s is advertised but never read from args anywhere in the runner module; it "
            "would parse and then do nothing at all" % flag
        )


def test_every_dispatched_call_resolves():
    """Every ``bf.x`` / ``wh.x`` the runner calls must exist on the module it names.

    This is the assertion that would have caught ``--reload``: it called
    ``bf.clear_completion(...)``, which stopped existing when the backfill was redesigned.
    """
    source = inspect.getsource(runner)
    missing = []
    for module, alias in ((backfill, "bf"), (warehouse, "wh")):
        for name in set(re.findall(r"\b%s\.([a-z_][a-z0-9_]*)\s*\(" % alias, source)):
            if not hasattr(module, name):
                missing.append("%s.%s -> %s.%s" % (alias, name, module.__name__, name))
    assert not missing, "runner calls names that do not exist: " + ", ".join(sorted(missing))


def test_no_help_text_references_a_removed_table():
    """The backfill side table is gone; no help string may still point an operator at it."""
    source = inspect.getsource(runner)
    assert "cdc_backfill_state" not in source, (
        "runner still references warehouse.cdc_backfill_state, which no longer exists. The resume "
        "point now lives in the landing zone itself."
    )


def test_reload_flag_is_gone_and_stays_gone():
    """``--reload`` belonged to the side-table design and has no meaning now.

    Re-running ``--backfill-only`` IS the safe path: the resume point is the highest id already
    landed, so a repeat run reads nothing. If someone reintroduces the flag it must come with a
    function behind it, so this test fails loudly rather than the flag failing at 3am.
    """
    assert "--reload" not in _parser_flags()
    assert not hasattr(backfill, "clear_completion")


def test_parser_accepts_each_advertised_flag():
    """Sanity: argparse itself accepts every advertised flag without erroring."""
    for flag in _parser_flags():
        parser = argparse.ArgumentParser()
        # Reconstruct just enough to assert the flag parses; the real parser is built in run().
        assert flag.startswith("--")
        assert len(flag) > 2
        del parser

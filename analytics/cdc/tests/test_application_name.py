"""Contract 05 §A.6: the loader must name itself to Postgres.

DWH found that its whole attributability design — `warehouse.access_audit.application_name`, and
`log_line_prefix`'s `%a` as the fallback when nothing calls `log_access()` — rested on a field it
had required of nobody. Three consumers set it because DWH wrote them; the three it did not write,
including this loader, set nothing.

Nothing fails while it is unset. The audit column exists, the function runs, and the row records
NULL for the one field naming WHICH service read the data. That is why it survived, and why a
regression guard has to be an explicit assertion rather than a consequence of something else
breaking.
"""

from __future__ import annotations

from bct_cdc import warehouse as wh


def test_the_contract_value_is_exact():
    """Asserted against the literal from contract 05 §A.6, not against "non-empty".

    A variant spelling (`cdc_loader`, `bct-cdc`) would pass any truthiness check and still break
    the join a reader performs against the clause's table.
    """
    assert wh.APPLICATION_NAME == "cdc-loader"


def test_connect_passes_the_application_name_to_psycopg2(monkeypatch):
    seen = {}

    class _Conn:
        autocommit = False

    def _fake_connect(dsn, **kwargs):
        seen["dsn"] = dsn
        seen["kwargs"] = kwargs
        return _Conn()

    monkeypatch.setattr(wh.psycopg2, "connect", _fake_connect)
    wh.connect("host=warehouse-db dbname=warehouse")

    assert seen["kwargs"].get("application_name") == "cdc-loader", (
        "the loader would reach the warehouse anonymous; access_audit.application_name records "
        "NULL. Contract 05 A.6. Got: %r" % seen["kwargs"].get("application_name")
    )


def test_it_is_set_on_source_connections_too(monkeypatch):
    """Both roles go through this one helper, so neither can be forgotten separately."""
    seen = []

    class _Conn:
        autocommit = False

    monkeypatch.setattr(
        wh.psycopg2, "connect",
        lambda dsn, **kw: (seen.append(kw.get("application_name")), _Conn())[1],
    )
    wh.connect("host=warehouse-db dbname=warehouse")
    wh.connect("host=postgres dbname=bct", autocommit=True)
    assert seen == ["cdc-loader", "cdc-loader"]

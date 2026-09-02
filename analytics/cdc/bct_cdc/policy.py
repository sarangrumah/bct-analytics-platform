"""Reads ``warehouse.column_policy`` and turns it into the per-table extraction plan.

The seam of frozen contract 05: **DWH owns the policy, Backend executes it.** Nothing in this file
decides what class a column is, and nothing invents a transform. If the policy says a column is
``personal``, it is hashed; if the policy has no row for a column, the loader exits non-zero.

Three defences apply to ``secret``, in order of strength:

1. The **publication carries a column list** built from this policy, so a ``secret`` column is never
   put on the wire by Postgres at all (:func:`publication_column_list`).
2. The backfill ``SELECT`` list is built from this policy, so a ``secret`` column is never named.
3. :meth:`MaskPlan.apply` drops any ``secret`` column that somehow reaches it.

Only the first is structural. The other two exist because a structural control with no backstop is
one migration away from being a policy control nobody re-checked.
"""

from __future__ import annotations

from dataclasses import dataclass

from .pdp_hash import pdp_hmac_sha256

#: The exact mapping of frozen contract 05. Not negotiable, and deliberately not inferred: if DWH
#: writes a (class, transform) pair that is not in this table, the loader refuses to run rather than
#: guessing which of the two fields it should believe.
VALID_CLASS_TRANSFORMS = {
    ("public", "none"),
    ("internal", "none"),
    ("personal", "hmac_sha256"),
    ("sensitive", "hmac_sha256_nullable"),
    ("secret", "drop"),
}

PDP_CLASSES = ("public", "internal", "personal", "sensitive", "secret")
TRANSFORMS = ("none", "hmac_sha256", "hmac_sha256_nullable", "drop")

#: Physical types a deterministic digest may be computed over. ``pdp_hmac_sha256`` takes ``str`` or
#: ``None`` and raises ``TypeError`` on anything else (contract 01 note 10), because there is no
#: cross-language-safe coercion: ``str(1.0)`` is ``'1.0'`` in Python and something else elsewhere,
#: and a ``jsonb`` blob's text form depends on key order.
TEXT_TYPES = frozenset({"text", "character varying", "character", "name", "citext"})


class UnclassifiedColumn(RuntimeError):
    """A column is about to be extracted and ``warehouse.column_policy`` has no row for it.

    Fatal by design (Backend brief, escalation trigger 1; contract 05 "Unclassified is a hard
    failure"). Defaulting to ``public`` is how personal data reaches a warehouse: the column that
    nobody classified is exactly the column nobody thought about.
    """


class PolicyInconsistent(RuntimeError):
    """The policy contains a (class, transform) pair contract 05 does not define."""


class UnhashableColumn(RuntimeError):
    """A column is marked ``hmac_sha256`` but its physical type is not text.

    Added at the Lead's ruling after ``res.partner.barcode`` (``jsonb`` *and* ``company_dependent``)
    was reclassified. The unclassified-column check would not have caught it: the column *was*
    classified, it was simply unhashable. Digesting a ``jsonb`` blob produces a key that changes
    whenever any single company's entry changes, so it is useless as a join key and it leaks how
    many companies hold a value -- while looking exactly like a working hash.

    This is the failure mode the check exists for: a silent wrong answer turned into a startup error.
    """


@dataclass(frozen=True)
class ColumnPolicy:
    source_table: str
    source_column: str
    pdp_class: str
    transform: str
    mask_null: bool

    @property
    def is_secret(self) -> bool:
        return self.pdp_class == "secret" or self.transform == "drop"

    def effective_transform(self) -> str:
        """Resolve ``hmac_sha256_nullable`` against ``mask_null`` into a concrete action.

        Returns one of ``none``, ``hash``, ``null``, ``drop``.
        """
        if self.is_secret:
            return "drop"
        if self.transform == "none":
            return "none"
        if self.transform == "hmac_sha256":
            return "hash"
        if self.transform == "hmac_sha256_nullable":
            return "null" if self.mask_null else "hash"
        raise PolicyInconsistent(
            "Unknown transform %r for %s.%s"
            % (self.transform, self.source_table, self.source_column)
        )


class MaskPlan:
    """The resolved per-column plan for one table, and the code that applies it."""

    def __init__(self, source_table: str, columns: dict, salt: str) -> None:
        self.source_table = source_table
        #: column -> 'none' | 'hash' | 'null'. ``secret`` columns are absent, never present as
        #: 'drop': the loader must not be able to name them.
        self.columns = columns
        self._salt = salt

    @property
    def select_columns(self) -> list:
        """Columns the loader may name in a ``SELECT``. Never contains a ``secret`` column."""
        return list(self.columns)

    def hashed_columns(self) -> list:
        return [c for c, t in self.columns.items() if t == "hash"]

    def nulled_columns(self) -> list:
        return [c for c, t in self.columns.items() if t == "null"]

    def apply(self, row: dict) -> dict:
        """Return ``row`` with masking applied, *before* it lands.

        Contract 01 and anti-pattern 7.5: no unmasked personal data ever reaches ``raw``. Masking
        happens here, in the loader, not in dbt and never in the BI layer -- there is deliberately
        no unmasking path anywhere downstream.
        """
        out = {}
        for column, value in row.items():
            action = self.columns.get(column)
            if action is None:
                # Not in the plan: either a secret column that leaked past the publication column
                # list, or a column added to the source since startup. Both are refusals, not
                # silent passes.
                raise UnclassifiedColumn(
                    "Column %s.%s has no entry in the resolved masking plan; refusing to land it."
                    % (self.source_table, column)
                )
            if action == "none":
                out[column] = value
            elif action == "null":
                out[column] = None
            elif action == "hash":
                if value is None:
                    out[column] = None
                else:
                    # Values arrive from pgoutput and from psycopg as text. A non-str here means a
                    # numeric column was classified `personal`, which contract 01 note 10 forbids.
                    out[column] = pdp_hmac_sha256(value, self._salt)
            else:  # pragma: no cover - effective_transform() cannot produce anything else
                raise PolicyInconsistent("Unknown action %r" % action)
        return out


class Policy:
    """The whole of ``warehouse.column_policy``, indexed for lookup."""

    def __init__(self, rows) -> None:
        self._by_table: dict = {}
        for row in rows:
            if row.pdp_class not in PDP_CLASSES:
                raise PolicyInconsistent(
                    "Unknown pdp_class %r for %s.%s"
                    % (row.pdp_class, row.source_table, row.source_column)
                )
            if row.transform not in TRANSFORMS:
                raise PolicyInconsistent(
                    "Unknown transform %r for %s.%s"
                    % (row.transform, row.source_table, row.source_column)
                )
            if (row.pdp_class, row.transform) not in VALID_CLASS_TRANSFORMS:
                raise PolicyInconsistent(
                    "Contract 05 forbids the pair (%s, %s) seen on %s.%s. The class-to-transform "
                    "mapping is not negotiable; fix warehouse.column_policy."
                    % (row.pdp_class, row.transform, row.source_table, row.source_column)
                )
            self._by_table.setdefault(row.source_table, {})[row.source_column] = row

    @property
    def tables(self) -> list:
        return sorted(self._by_table)

    def get(self, table: str, column: str):
        return self._by_table.get(table, {}).get(column)

    def secret_columns(self, table: str) -> list:
        return sorted(
            c for c, p in self._by_table.get(table, {}).items() if p.is_secret
        )

    def plan(self, table: str, actual_columns, salt: str, column_types=None) -> MaskPlan:
        """Resolve the plan for ``table`` against the columns the source actually has.

        ``actual_columns`` comes from ``information_schema`` on the source, not from the policy: a
        column present in the database but missing from the policy is precisely the case that must
        hard-fail, and comparing the policy against itself would never find it.

        ``column_types`` maps column -> ``information_schema`` ``data_type``. When supplied, every
        column resolving to a hash is checked against :data:`TEXT_TYPES`, and a non-text one is
        fatal (:class:`UnhashableColumn`). That check catches the case the unclassified check
        cannot: a column that *was* classified, just not hashably.
        """
        table_policy = self._by_table.get(table)
        if not table_policy:
            raise UnclassifiedColumn(
                "warehouse.column_policy has no rows at all for source table %r. Refusing to "
                "extract an unclassified table." % table
            )
        missing = [c for c in actual_columns if c not in table_policy]
        if missing:
            raise UnclassifiedColumn(
                "warehouse.column_policy has no row for %s: %s. "
                "Unclassified is a hard failure (contract 05); it is never defaulted to 'public'. "
                "Add the classification in custom_pdp_core and re-seed the policy."
                % (table, ", ".join("%s.%s" % (table, c) for c in sorted(missing)))
            )
        columns = {}
        unhashable = []
        for column in actual_columns:
            action = table_policy[column].effective_transform()
            if action == "drop":
                continue  # secret: omitted entirely, never nameable
            if action == "hash" and column_types is not None:
                physical = column_types.get(column)
                if physical not in TEXT_TYPES:
                    unhashable.append((column, physical))
            columns[column] = action
        if unhashable:
            raise UnhashableColumn(
                "warehouse.column_policy marks these %s columns hmac_sha256 but their physical type "
                "is not text: %s. A digest over a non-text type is not a stable join key and the "
                "loader refuses to produce one. Reclassify the column (sensitive + mask_null is the "
                "usual answer) rather than coercing it to text here."
                % (table, ", ".join("%s (%s)" % (c, t) for c, t in sorted(unhashable)))
            )
        return MaskPlan(table, columns, salt)


def publication_column_list(plan: MaskPlan, key_columns=("id",)) -> list:
    """Columns to name in ``CREATE PUBLICATION ... FOR TABLE t (...)``.

    A publication column list is the structural control behind contract 01's "``secret`` is dropped
    at extraction": Postgres does not put the column on the wire, so no bug in this loader can land
    it. Replica-identity columns must be present or Postgres rejects the publication.
    """
    columns = list(plan.select_columns)
    for key in key_columns:
        if key not in columns:
            raise UnclassifiedColumn(
                "Replica identity column %r of %s is not in the masking plan; a publication "
                "column list without it is rejected by Postgres." % (key, plan.source_table)
            )
    return columns

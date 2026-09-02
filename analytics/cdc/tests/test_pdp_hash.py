"""The loader's half of the cross-language digest contract.

These assert the *same four vectors* as
``addons/custom_pdp_masking/tests/test_pdp_masking.py::TestPdpHash::test_known_answer_vectors``.
Two implementations of one specification is exactly the situation contract 05 warns about, and the
only thing that keeps them honest is a shared set of numbers that both sides assert independently.

The negative vectors matter as much as the positive ones. A loader that reimplemented the digest as
``sha256(salt + value)``, or that trimmed or lower-cased its input, would produce a perfectly stable
64-character hex string that simply does not join to anything Odoo produced -- and nothing would
raise. These tests are what turns that into a red build.
"""

from __future__ import annotations

import hashlib
import unicodedata

import pytest

from bct_cdc.pdp_hash import (
    KNOWN_ANSWER_VECTORS,
    PDP_DIGEST_ALGORITHM,
    pdp_hmac_sha256,
    self_test,
)

SALT = "bct-demo-salt"


@pytest.mark.parametrize("value,salt,expected", KNOWN_ANSWER_VECTORS)
def test_known_answer_vectors(value, salt, expected):
    assert pdp_hmac_sha256(value, salt) == expected


def test_self_test_passes():
    self_test()


def test_algorithm_name_matches_the_odoo_module():
    # The startup check compares this exactly, not by prefix. It caught a real mismatch on its
    # first run against the live module ("hmac-sha256" vs the full name), which is the entire
    # reason the comparison is exact.
    assert PDP_DIGEST_ALGORITHM == "hmac-sha256/utf8/lowerhex"


def test_output_shape():
    digest = pdp_hmac_sha256("Budi Santoso", SALT)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_determinism_within_a_tenant():
    assert pdp_hmac_sha256("Budi Santoso", SALT) == pdp_hmac_sha256("Budi Santoso", SALT)


def test_separation_across_tenants():
    assert pdp_hmac_sha256("Budi Santoso", SALT) != pdp_hmac_sha256("Budi Santoso", "other-tenant-salt")


def test_null_in_null_out():
    assert pdp_hmac_sha256(None, SALT) is None


def test_empty_string_is_null_not_a_shared_digest():
    # Hashing "" would give every empty cell one shared non-NULL digest, i.e. a fabricated join key
    # that makes unrelated rows look like the same person.
    assert pdp_hmac_sha256("", SALT) is None


def test_non_str_raises_type_error():
    for value in (1, 1.0, True, b"bytes", {"a": 1}):
        with pytest.raises(TypeError):
            pdp_hmac_sha256(value, SALT)


def test_empty_salt_raises_value_error():
    with pytest.raises(ValueError):
        pdp_hmac_sha256("Budi Santoso", "")


def test_non_str_salt_raises_type_error():
    with pytest.raises(TypeError):
        pdp_hmac_sha256("Budi Santoso", None)


# -- negative vectors: guard against a plausible-but-wrong reimplementation --------------------


def test_is_not_salt_concatenation():
    concatenated = hashlib.sha256((SALT + "Budi Santoso").encode("utf-8")).hexdigest()
    assert pdp_hmac_sha256("Budi Santoso", SALT) != concatenated


def test_is_not_value_concatenation():
    concatenated = hashlib.sha256(("Budi Santoso" + SALT).encode("utf-8")).hexdigest()
    assert pdp_hmac_sha256("Budi Santoso", SALT) != concatenated


def test_no_trimming():
    assert pdp_hmac_sha256(" Budi Santoso ", SALT) != pdp_hmac_sha256("Budi Santoso", SALT)


def test_no_case_folding():
    assert pdp_hmac_sha256("budi santoso", SALT) != pdp_hmac_sha256("Budi Santoso", SALT)


def test_no_unicode_normalisation():
    # Written as explicit codepoints, not as source literals: two visually identical "Andre" strings
    # in a source file can silently normalise to the same bytes in an editor, which would make this
    # test pass for the wrong reason.
    composed = "André"        # U+00E9 LATIN SMALL LETTER E WITH ACUTE
    decomposed = "André"     # "e" + U+0301 COMBINING ACUTE ACCENT
    assert composed != decomposed
    assert unicodedata.normalize("NFC", decomposed) == composed
    # NFC would make these equal. The contract says no normalisation, so the digests must differ.
    assert pdp_hmac_sha256(composed, SALT) != pdp_hmac_sha256(decomposed, SALT)

"""Tenant-identity invariants — the injectivity proof for due diligence.

If ``point_id`` were not injective, one tenant's write could overwrite another's
Qdrant point. The hypothesis test below is that proof, run in CI.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from skylize.memory import identity
from skylize.memory.identity import InvalidIdentifier

# Include ':' and other separators that broke the old concatenation scheme.
_component = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FFF),
    min_size=0,
    max_size=48,
)


@settings(max_examples=500)
@given(a_org=_component, a_doc=_component, b_org=_component, b_doc=_component)
def test_point_id_is_injective(a_org: str, a_doc: str, b_org: str, b_doc: str) -> None:
    """Distinct (org_id, doc_id) pairs → distinct ids; equal pairs → equal ids."""
    same_pair = (a_org, a_doc) == (b_org, b_doc)
    same_id = identity.point_id(a_org, a_doc) == identity.point_id(b_org, b_doc)
    assert same_id == same_pair


def test_point_id_defeats_old_delimiter_aliasing() -> None:
    """The classic collision the old f'{org}:{doc}' scheme allowed is gone."""
    assert identity.point_id("acme:eu", "report") != identity.point_id("acme", "eu:report")


@pytest.mark.parametrize(
    "bad",
    ["a:b", "UPPER", "has space", "", "-leading", "dot.dot", "sl/ash", "a" * 129, "wörd"],
)
def test_validate_identifier_rejects(bad: str) -> None:
    with pytest.raises(InvalidIdentifier):
        identity.validate_identifier(bad, field="org_id")


@pytest.mark.parametrize(
    "good", ["org_a", "org-acme-11111111", "a", "a1_b-2", "z" * 128, "getting-started"]
)
def test_validate_identifier_accepts(good: str) -> None:
    assert identity.validate_identifier(good, field="org_id") == good


def test_content_doc_id_is_stable_and_content_addressed() -> None:
    same_a = identity.content_doc_id(b"hello world", prefix="upload")
    same_b = identity.content_doc_id(b"hello world", prefix="upload")
    other = identity.content_doc_id(b"goodbye world", prefix="upload")
    assert same_a == same_b
    assert same_a != other
    assert same_a.startswith("upload/")


def test_chunk_point_id_matches_point_id_of_chunk_doc_id() -> None:
    org, doc = "org_a", "upload/abc"
    assert identity.chunk_point_id(org, doc, 3) == identity.point_id(
        org, identity.chunk_doc_id(doc, 3)
    )

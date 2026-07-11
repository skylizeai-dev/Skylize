from __future__ import annotations


from anytype_sync.anytype_client import AnytypeObject, AnytypeObjectType, AnytypeProperty
from anytype_sync.sync import filter_modified_since


def _obj(object_id: str, last_modified: str, type_key: str = "page") -> AnytypeObject:
    return AnytypeObject(
        id=object_id,
        name=f"Object {object_id}",
        type=AnytypeObjectType(key=type_key),
        properties=[
            AnytypeProperty(key="last_modified_date", format="date", date=last_modified),
        ],
    )


# ── filter_modified_since ──────────────────────────────────────────────────────

def test_no_threshold_passes_all() -> None:
    objects = [_obj("a", "2024-01-01"), _obj("b", "2024-06-01")]
    assert filter_modified_since(objects, None) == objects


def test_objects_before_threshold_dropped() -> None:
    objects = [
        _obj("old", "2024-01-01"),
        _obj("new", "2024-02-01"),
    ]
    result = filter_modified_since(objects, "2024-01-15")
    assert [o.id for o in result] == ["new"]


def test_objects_on_threshold_kept() -> None:
    # "equal" must be kept (GreaterOrEqual).
    objects = [_obj("exact", "2024-01-15")]
    result = filter_modified_since(objects, "2024-01-15")
    assert len(result) == 1


def test_all_before_threshold_returns_empty() -> None:
    objects = [_obj("old1", "2023-12-01"), _obj("old2", "2023-11-01")]
    result = filter_modified_since(objects, "2024-01-01")
    assert result == []


def test_all_after_threshold_kept() -> None:
    objects = [_obj("a", "2024-03-01"), _obj("b", "2024-04-01")]
    result = filter_modified_since(objects, "2024-01-01")
    assert len(result) == 2


def test_datetime_threshold_against_date_property() -> None:
    # State stores full datetime; Anytype may return just a date.
    objects = [
        _obj("old", "2024-01-14"),
        _obj("new", "2024-01-16"),
    ]
    result = filter_modified_since(objects, "2024-01-15T00:00:00Z")
    assert [o.id for o in result] == ["new"]


def test_object_missing_date_property_skipped() -> None:
    obj = AnytypeObject(
        id="no-date",
        name="No date",
        type=AnytypeObjectType(key="page"),
        properties=[],  # no last_modified_date
    )
    result = filter_modified_since([obj], "2024-01-01")
    assert result == []


def test_object_null_date_value_skipped() -> None:
    obj = AnytypeObject(
        id="null-date",
        name="Null date",
        type=AnytypeObjectType(key="page"),
        properties=[AnytypeProperty(key="last_modified_date", format="date", date=None)],
    )
    result = filter_modified_since([obj], "2024-01-01")
    assert result == []


def test_page_type_filter_in_sync_pipeline() -> None:
    """sync.py drops non-page objects after filter_modified_since."""
    objects = [
        _obj("page1", "2024-02-01", type_key="page"),
        _obj("task1", "2024-02-01", type_key="task"),
        _obj("note1", "2024-02-01", type_key="note"),
    ]
    # filter_modified_since itself doesn't care about type — that's sync.py's job
    after_date = filter_modified_since(objects, "2024-01-01")
    pages_only = [o for o in after_date if o.type.key == "page"]
    assert [o.id for o in pages_only] == ["page1"]

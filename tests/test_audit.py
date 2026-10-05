from app.services.audit import canonical_payload, compute_event_hash


def test_hash_changes_with_previous_hash_or_payload():
    payload = {"action": "task.created", "resource_id": "abc"}
    first = compute_event_hash("", payload)
    assert len(first) == 64
    assert compute_event_hash("previous", payload) != first
    assert compute_event_hash("", {"action": "changed"}) != first


def test_canonical_payload_is_stable():
    assert canonical_payload({"b": 2, "a": 1}) == '{"a":1,"b":2}'

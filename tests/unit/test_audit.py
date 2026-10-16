from beomcp.audit import AuditLogger, digest_params


def test_digest_is_stable_and_opaque():
    a = digest_params({"customer_iri": "https://example.org/id/a"})
    b = digest_params({"customer_iri": "https://example.org/id/a"})
    assert a == b
    assert len(a) == 16
    assert "example.org" not in a


def test_digest_differs_per_payload():
    assert digest_params({"a": 1}) != digest_params({"a": 2})


def test_log_emits_six_fields_and_no_result_body():
    seen: list[dict] = []
    log = AuditLogger(sink=seen.append)
    log.log(tool="run_query", params={"name": "x"}, principal="agent:1",
            duration_ms=12.3456, row_count=3, ok=True)
    assert set(seen[0]) == {
        "tool", "params_digest", "principal", "duration_ms", "row_count", "ok",
    }
    assert "rows" not in seen[0]
    assert seen[0]["duration_ms"] == 12.346

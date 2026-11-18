from mia.observatory.store import ObservatoryStore


def test_event_and_metric_are_persisted(tmp_path):
    store = ObservatoryStore(tmp_path / "test.sqlite3")
    event_id = store.record_event(
        category="validation",
        event_type="test_run",
        summary="Foundation test run",
        day_index=0,
        layer_id="SPS-09",
        payload={"passed": 3},
    )
    sample_id = store.record_metric(
        day_index=0,
        metric_name="test_pass_rate",
        value=100,
        unit="percent",
        benchmark_id="foundation-tests-v1",
        source_event_id=event_id,
    )
    assert len(event_id) == 32
    assert len(sample_id) == 32
    assert store.summary()["event_count"] == 1
    assert store.summary()["metric_sample_count"] == 1
    assert store.list_events()[0]["event_id"] == event_id
    assert store.list_metrics("test_pass_rate")[0]["value"] == 100


def test_negative_day_index_is_rejected(tmp_path):
    store = ObservatoryStore(tmp_path / "test.sqlite3")
    try:
        store.record_metric(day_index=-1, metric_name="bad", value=1, unit="score")
    except ValueError:
        pass
    else:
        raise AssertionError("Negative day index should be rejected")

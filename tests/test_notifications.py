"""MIA-LIFE-09: notification center policy tests (fake clock, local SQLite, no network)."""

from datetime import UTC, datetime, timedelta

import pytest

from mia.life.notifications import NotificationCenter, NotificationNotFound

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def center(tmp_path):
    return NotificationCenter(tmp_path / "n.sqlite3")


def send(center, key="a", now=T0, **kw):
    args = dict(source_type="task", source_ref=f"task-{key}", title=f"Title {key}", body="Body",
                reason="Because the task is due", relevance=0.8, now=now)
    args.update(kw)
    return center.submit(**args)


def ids(center, now, states=("pending", "read")):
    return [i["notification_id"] for i in center.list(states=states, now=now)]


# ---- validation -----------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"timezone": "Mars/Base"}, {"timezone": "../etc/passwd"}, {"timezone": ""}, {"nope": 1},
    {"max_per_day": -1}, {"max_per_day": True}, {"min_relevance": 1.5}, {"voice_enabled": "yes"},
    {"quiet_start_minute": 1440},
])
def test_settings_validation_rejects_bad_values(center, bad):
    with pytest.raises(ValueError):
        center.update_settings(bad)


def test_quiet_window_needs_distinct_start_and_end(center):
    with pytest.raises(ValueError):
        center.update_settings({"quiet_hours_enabled": True, "quiet_start_minute": 60, "quiet_end_minute": 60})


@pytest.mark.parametrize("kw", [
    {"reason": "  "}, {"source_ref": ""}, {"title": ""}, {"source_type": "Bad Type"},
    {"priority": "loud"}, {"relevance": 2}, {"relevance": True}, {"expires_in_hours": 0},
])
def test_submit_requires_reason_source_and_sane_values(center, kw):
    with pytest.raises(ValueError):
        send(center, **kw)


def test_control_characters_are_stripped_and_text_is_clamped(center):
    out = send(center, title="Hi\x00\x07 there", body="x" * 5000)
    item = center.list(now=T0)[0]
    assert item["title"] == "Hi there" and len(item["body"]) == 2000
    assert out["decision"] == "queued"


# ---- delivery, channels, relevance --------------------------------------------------------------
def test_normal_notification_is_delivered_immediately_to_the_inbox(center):
    send(center)
    item = center.list(now=T0)[0]
    assert item["state"] == "pending" and item["channels"] == ["inbox"] and item["released_at"]


def test_no_channel_or_disabled_center_suppresses_with_a_reason(center):
    center.update_settings({"inbox_enabled": False})
    assert send(center)["reason"] == "no_channel_enabled"
    center.update_settings({"inbox_enabled": True, "enabled": False})
    assert send(center, "b")["reason"] == "notifications_off"
    assert center.list(now=T0) == []


def test_relevance_floor_suppresses_but_urgent_is_exempt(center):
    low = send(center, relevance=0.1)
    assert low["decision"] == "suppressed" and low["reason"] == "below_relevance_floor"
    assert send(center, "u", priority="urgent", relevance=0.0)["decision"] == "queued"
    assert center.counts(T0) == {"pending": 1, "suppressed": 1}


# ---- de-duplication -------------------------------------------------------------------------------
def test_duplicate_is_merged_not_repeated(center):
    first = send(center)
    again = send(center, now=T0 + timedelta(minutes=5), body="Updated body")
    assert again == {"decision": "deduplicated", "notification_id": first["notification_id"], "reason": "already_open"}
    items = center.list(now=T0 + timedelta(minutes=5))
    assert len(items) == 1 and items[0]["occurrences"] == 2 and items[0]["body"] == "Updated body"


def test_dismissed_notification_stays_quiet_during_cooldown_then_may_return(center):
    first = send(center)
    center.dismiss(first["notification_id"], now=T0)
    repeat = send(center, now=T0 + timedelta(days=1))
    assert repeat["decision"] == "suppressed" and repeat["reason"] == "recently_dismissed"
    assert center.list(now=T0 + timedelta(days=1)) == []
    later = send(center, now=T0 + timedelta(days=8), )
    assert later["decision"] == "queued"


def test_urgent_ignores_the_dismiss_cooldown(center):
    first = send(center, priority="urgent")
    center.dismiss(first["notification_id"], now=T0)
    assert send(center, priority="urgent", now=T0 + timedelta(hours=1))["decision"] == "queued"


# ---- quiet hours, timezones, DST ------------------------------------------------------------------
def quiet(center, tz="UTC", start=22 * 60, end=7 * 60, **extra):
    center.update_settings({"timezone": tz, "quiet_hours_enabled": True, "quiet_start_minute": start,
                            "quiet_end_minute": end, "default_expiry_hours": 48, **extra})


def test_quiet_hours_hold_until_the_window_ends_across_midnight(center):
    quiet(center)
    night = datetime(2026, 6, 1, 23, 30, tzinfo=UTC)
    send(center, now=night)
    assert center.list(now=night) == []
    assert center.counts(night) == {"held": 1}
    assert ids(center, datetime(2026, 6, 2, 6, 59, tzinfo=UTC)) == []
    assert len(ids(center, datetime(2026, 6, 2, 7, 0, tzinfo=UTC))) == 1


def test_quiet_hours_use_the_owner_timezone(center):
    quiet(center, tz="Asia/Karachi")  # UTC+5, no DST
    # 18:30 UTC is 23:30 in Karachi, inside quiet hours; 02:00 UTC is 07:00 local, the end.
    send(center, now=datetime(2026, 6, 1, 18, 30, tzinfo=UTC))
    assert ids(center, datetime(2026, 6, 1, 20, 0, tzinfo=UTC)) == []
    assert len(ids(center, datetime(2026, 6, 2, 2, 0, tzinfo=UTC))) == 1
    # 12:00 UTC is 17:00 local: not quiet, delivered immediately.
    assert send(center, "day", now=datetime(2026, 6, 2, 12, 0, tzinfo=UTC))["decision"] == "queued"
    assert "day" in {i["source_ref"].removeprefix("task-") for i in center.list(now=datetime(2026, 6, 2, 12, 0, tzinfo=UTC))}


def test_spring_forward_quiet_end_is_11_utc_not_12(center):
    quiet(center, tz="America/New_York")
    start = datetime(2026, 3, 8, 3, 0, tzinfo=UTC)  # 22:00 EST on 7 March, before the 2:00 jump
    send(center, now=start)
    assert ids(center, datetime(2026, 3, 8, 10, 59, tzinfo=UTC)) == []
    assert len(ids(center, datetime(2026, 3, 8, 11, 0, tzinfo=UTC))) == 1  # 07:00 EDT


def test_fall_back_quiet_end_is_12_utc_not_11(center):
    quiet(center, tz="America/New_York")
    start = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # 00:00 EDT, before the 2:00 -> 1:00 repeat
    send(center, now=start)
    assert ids(center, datetime(2026, 11, 1, 11, 30, tzinfo=UTC)) == []
    assert len(ids(center, datetime(2026, 11, 1, 12, 0, tzinfo=UTC))) == 1  # 07:00 EST


def test_urgent_bypasses_quiet_hours_unless_the_owner_turns_that_off(center):
    quiet(center)
    night = datetime(2026, 6, 1, 23, 30, tzinfo=UTC)
    assert send(center, priority="urgent", now=night)["decision"] == "queued"
    assert len(center.list(now=night)) == 1
    center.update_settings({"urgent_bypasses_quiet": False})
    send(center, "u2", priority="urgent", now=night)
    assert len(center.list(now=night)) == 1  # the second stays held


def test_notification_that_would_expire_during_quiet_hours_is_suppressed(center):
    quiet(center)
    night = datetime(2026, 6, 1, 23, 0, tzinfo=UTC)
    out = send(center, now=night, expires_in_hours=2)
    assert out["decision"] == "suppressed" and out["reason"] == "would_expire_during_quiet_hours"


# ---- caps and gaps -----------------------------------------------------------------------------------
def test_daily_cap_defers_the_rest_to_the_next_local_day_but_not_urgent(center):
    center.update_settings({"max_per_day": 2, "timezone": "Asia/Karachi", "default_expiry_hours": 72})
    now = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)  # 11:00 Karachi
    for key in "abc":
        send(center, key, now=now)
    assert len(center.list(now=now)) == 2 and center.counts(now)["held"] == 1
    send(center, "u", now=now, priority="urgent")
    assert len(center.list(now=now)) == 3  # urgent is not capped
    midnight_local = datetime(2026, 6, 1, 19, 0, tzinfo=UTC)  # 00:00 Karachi on 2 June
    assert len(center.list(now=midnight_local - timedelta(minutes=1))) == 3
    assert len(center.list(now=midnight_local)) == 4


def test_minimum_gap_staggers_non_urgent_notifications(center):
    center.update_settings({"min_gap_minutes": 30})
    send(center, "a")
    send(center, "b")
    assert len(center.list(now=T0)) == 1
    assert len(center.list(now=T0 + timedelta(minutes=29))) == 1
    assert len(center.list(now=T0 + timedelta(minutes=30))) == 2


# ---- expiry and offline catch-up ---------------------------------------------------------------------
def test_expiry_applies_after_downtime_without_any_background_process(center):
    send(center, expires_in_hours=6)
    assert len(center.list(now=T0 + timedelta(hours=5))) == 1
    after_outage = T0 + timedelta(days=3)  # the app was closed the whole time
    assert center.list(now=after_outage) == []
    assert center.counts(after_outage) == {"expired": 1}


def test_state_survives_restart(tmp_path):
    first = NotificationCenter(tmp_path / "n.sqlite3")
    first.update_settings({"timezone": "Asia/Karachi", "voice_enabled": True})
    send(first)
    second = NotificationCenter(tmp_path / "n.sqlite3")
    assert second.get_settings().timezone == "Asia/Karachi"
    assert len(second.list(now=T0)) == 1 and second.list(now=T0)[0]["channels"] == ["inbox", "voice"]


# ---- snooze, read, dismiss -----------------------------------------------------------------------------
def test_snooze_hides_then_returns(center):
    n = send(center)["notification_id"]
    center.snooze(n, minutes=30, now=T0)
    assert center.list(now=T0 + timedelta(minutes=29)) == []
    assert ids(center, T0 + timedelta(minutes=30)) == [n]


def test_snooze_ending_inside_quiet_hours_is_held_again(center):
    quiet(center, start=22 * 60, end=7 * 60)
    n = send(center, now=datetime(2026, 6, 1, 20, 0, tzinfo=UTC))["notification_id"]
    center.snooze(n, until=datetime(2026, 6, 1, 23, 0, tzinfo=UTC), now=datetime(2026, 6, 1, 20, 0, tzinfo=UTC))
    assert center.list(now=datetime(2026, 6, 1, 23, 30, tzinfo=UTC)) == []
    assert ids(center, datetime(2026, 6, 2, 7, 0, tzinfo=UTC)) == [n]


@pytest.mark.parametrize("kw", [{"minutes": 0}, {"minutes": -5}, {"minutes": 60 * 24 * 31}, {"until": datetime(2026, 6, 1, 12, 30)}])
def test_snooze_rejects_bad_times(center, kw):
    n = send(center)["notification_id"]
    with pytest.raises(ValueError):
        center.snooze(n, now=T0, **kw)


def test_snooze_past_expiry_is_rejected(center):
    n = send(center, expires_in_hours=1)["notification_id"]
    with pytest.raises(ValueError):
        center.snooze(n, minutes=120, now=T0)


def test_read_dismiss_and_invalid_transitions(center):
    n = send(center)["notification_id"]
    assert center.mark_read(n, now=T0)["state"] == "read"
    assert [i["state"] for i in center.list(now=T0)] == ["read"]
    center.dismiss(n, now=T0)
    for action in (center.mark_read, center.dismiss):
        with pytest.raises(ValueError):
            action(n, now=T0)
    with pytest.raises(ValueError):
        center.snooze(n, minutes=5, now=T0)
    with pytest.raises(NotificationNotFound):
        center.dismiss("ntf-missing", now=T0)


# ---- transparency and voice --------------------------------------------------------------------------------
def test_explain_shows_why_source_evidence_and_history(center):
    n = send(center, evidence=[{"type": "task", "ref": "task-a"}], reason="Due tomorrow")["notification_id"]
    send(center, now=T0 + timedelta(minutes=1))
    center.snooze(n, minutes=10, now=T0 + timedelta(minutes=2))
    info = center.explain(n, now=T0 + timedelta(minutes=3))
    assert info["why"] == "Due tomorrow" and info["source"] == {"type": "task", "ref": "task-a"}
    assert info["evidence"] == [{"type": "task", "ref": "task-a"}] and info["occurrences"] == 2
    assert [h["event"] for h in info["history"]] == ["held", "delivered", "deduplicated", "snoozed"]
    with pytest.raises(NotificationNotFound):
        center.explain("ntf-missing")


def test_voice_digest_is_opt_in_once_only_and_never_during_quiet_hours(center):
    send(center, "a")
    assert center.voice_digest(now=T0) == []  # voice channel is off by default
    center.update_settings({"voice_enabled": True})
    send(center, "b", priority="urgent")
    send(center, "c")
    digest = center.voice_digest(limit=2, now=T0)
    assert [d["source_ref"] for d in digest][0] == "task-b" and len(digest) == 2
    again = center.voice_digest(limit=5, now=T0)
    assert all(d["source_ref"] != "task-b" for d in again)
    assert center.voice_digest(now=T0) == []
    quiet(center, voice_enabled=True)
    night = datetime(2026, 6, 1, 23, 30, tzinfo=UTC)
    send(center, "d", now=night)
    assert center.voice_digest(now=night) == []  # held, so never spoken at night

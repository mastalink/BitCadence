from datetime import datetime, timedelta, timezone

import pytest

from mco.scheduler import (
    ScheduleConfigError,
    ScheduleState,
    due_schedules,
    exhaustion_reason,
    format_duration,
    is_due,
    next_cron_time,
    next_run_at,
    parse_config,
    parse_cron,
    parse_duration,
    parse_timestamp,
)


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ── duration parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("30s", 30), ("15m", 900), ("2h", 7200), ("1d", 86400), ("1w", 604800),
    ("1.5h", 5400), (" 45m ", 2700), ("90", 90),
])
def test_parse_duration_accepts_common_forms(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "soon", "5x", "-10m", "0", "0s", True])
def test_parse_duration_rejects_junk(bad):
    # A typo'd interval must never coerce to zero - that would spin a metered
    # model in a tight loop.
    with pytest.raises(ScheduleConfigError):
        parse_duration(bad)


# ── cron parsing ──────────────────────────────────────────────────────────────

def test_parse_cron_basic_fields():
    expr = parse_cron("30 4 * * *")
    assert expr.minutes == frozenset({30})
    assert expr.hours == frozenset({4})
    assert len(expr.days) == 31 and len(expr.months) == 12


def test_parse_cron_step_and_range_and_list():
    assert parse_cron("*/15 * * * *").minutes == frozenset({0, 15, 30, 45})
    assert parse_cron("0 9-17 * * *").hours == frozenset(range(9, 18))
    assert parse_cron("0 0 * * 1,3,5").weekdays == frozenset({1, 3, 5})
    assert parse_cron("0 8-18/4 * * *").hours == frozenset({8, 12, 16})


def test_parse_cron_shortcuts():
    assert parse_cron("@daily").raw == "0 0 * * *"
    assert parse_cron("@hourly").minutes == frozenset({0})
    with pytest.raises(ScheduleConfigError, match="unknown shortcut"):
        parse_cron("@fortnightly")


def test_parse_cron_normalizes_sunday_seven_to_zero():
    # Both 0 and 7 mean Sunday in cron; matches() only understands 0.
    assert parse_cron("0 0 * * 7").weekdays == frozenset({0})


@pytest.mark.parametrize("bad", [
    "* * * *",            # too few fields
    "* * * * * *",        # too many
    "60 * * * *",         # minute out of range
    "* 24 * * *",         # hour out of range
    "0 0 0 * *",          # day-of-month starts at 1
    "0 0 * 13 *",         # month out of range
    "abc * * * *",        # not a number
    "*/0 * * * *",        # zero step
    "",
])
def test_parse_cron_rejects_invalid(bad):
    with pytest.raises(ScheduleConfigError):
        parse_cron(bad)


# ── next fire time ────────────────────────────────────────────────────────────

def test_next_cron_time_is_strictly_after():
    expr = parse_cron("0 3 * * *")
    # Standing exactly on a match must advance, not return the same instant -
    # otherwise a tick at 03:00 would fire forever.
    assert next_cron_time(expr, _utc(2026, 7, 1, 3, 0)) == _utc(2026, 7, 2, 3, 0)


def test_next_cron_time_same_day_when_later():
    assert next_cron_time(parse_cron("0 3 * * *"), _utc(2026, 7, 1, 1, 0)) == _utc(2026, 7, 1, 3, 0)


def test_next_cron_time_crosses_month_boundary():
    # 1st of the month at midnight, standing in the middle of July.
    assert next_cron_time(parse_cron("0 0 1 * *"), _utc(2026, 7, 15, 12)) == _utc(2026, 8, 1)


def test_next_cron_time_finds_leap_day():
    # Feb 29 only exists in leap years - the day-skipping optimization must not
    # blow past it.
    result = next_cron_time(parse_cron("0 0 29 2 *"), _utc(2026, 3, 1))
    assert (result.year, result.month, result.day) == (2028, 2, 29)


def test_next_cron_time_weekday_matching():
    # Every Monday at 09:00. 2026-07-01 is a Wednesday.
    result = next_cron_time(parse_cron("0 9 * * 1"), _utc(2026, 7, 1, 12))
    assert result.weekday() == 0 and result.hour == 9


def test_cron_day_fields_are_unioned_not_intersected():
    # Standard cron quirk: when BOTH day-of-month and day-of-week are
    # restricted, a match on either fires.
    expr = parse_cron("0 0 1 * 5")  # 1st of month OR any Friday
    assert expr.day_union is True
    assert expr.matches(_utc(2026, 7, 1))       # the 1st (a Wednesday)
    assert expr.matches(_utc(2026, 7, 3))       # a Friday
    assert not expr.matches(_utc(2026, 7, 2))   # neither


def test_cron_with_timezone_resolves_in_that_zone():
    expr = parse_cron("0 3 * * *")
    result = next_cron_time(expr, _utc(2026, 7, 1, 0, 0), tz="America/New_York")
    assert result.hour == 3
    # 03:00 in New York is 07:00 UTC in July (EDT).
    assert result.astimezone(timezone.utc).hour == 7


def test_unknown_timezone_is_rejected():
    with pytest.raises(ScheduleConfigError, match="unknown timezone"):
        next_cron_time(parse_cron("@daily"), _utc(2026, 7, 1), tz="Mars/Olympus_Mons")


# ── config validation ─────────────────────────────────────────────────────────

def _config(**overrides):
    base = {
        "launchers": {
            "audit": {"role": "reviewer", "title": "Audit", "instructions": "Check deps"},
        },
        "schedules": {
            "nightly": {"launcher": "audit", "cron": "0 3 * * *"},
        },
    }
    base.update(overrides)
    return base


def test_parse_config_happy_path():
    launchers, schedules = parse_config(_config())
    assert launchers["audit"].role == "reviewer"
    assert schedules["nightly"].cron is not None
    assert schedules["nightly"].kind == "schedule"


def test_launcher_requires_a_target_kind():
    with pytest.raises(ScheduleConfigError, match="needs one of 'role'"):
        parse_config({"launchers": {"x": {"title": "no target"}}, "schedules": {}})


def test_launcher_rejects_both_role_and_workflow():
    with pytest.raises(ScheduleConfigError, match="exactly one of"):
        parse_config({
            "launchers": {"x": {"role": "codex", "workflow": "w.yaml", "title": "t"}},
            "schedules": {},
        })


def test_launcher_rejects_unknown_field():
    with pytest.raises(ScheduleConfigError, match="unknown field"):
        parse_config({
            "launchers": {"x": {"role": "codex", "title": "t", "colour": "blue"}},
            "schedules": {},
        })


def test_schedule_must_reference_a_defined_launcher():
    with pytest.raises(ScheduleConfigError, match="unknown launcher"):
        parse_config(_config(schedules={"n": {"launcher": "ghost", "every": "1h"}}))


def test_schedule_needs_exactly_one_trigger():
    with pytest.raises(ScheduleConfigError, match="either 'cron' or 'every'"):
        parse_config(_config(schedules={"n": {"launcher": "audit", "cron": "@daily", "every": "1h"}}))
    with pytest.raises(ScheduleConfigError, match="needs a 'cron'"):
        parse_config(_config(schedules={"n": {"launcher": "audit"}}))


def test_schedule_rejects_bad_overlap_policy():
    with pytest.raises(ScheduleConfigError, match="overlap must be"):
        parse_config(_config(schedules={"n": {"launcher": "audit", "every": "1h", "overlap": "queue"}}))


def test_loop_must_declare_a_bound():
    # The governance rule this whole distinction exists for: an unbounded
    # self-repeating agent task is refused at parse time, not at 3am.
    with pytest.raises(ScheduleConfigError, match="must declare 'max_iterations'"):
        parse_config(_config(schedules={}, loops={"forever": {"launcher": "audit", "every": "5m"}}))


def test_loop_accepts_either_bound():
    _, schedules = parse_config(_config(schedules={}, loops={
        "counted": {"launcher": "audit", "every": "5m", "max_iterations": 3},
        "dated": {"launcher": "audit", "every": "5m", "until": "2026-12-31T00:00:00Z"},
    }))
    assert schedules["counted"].is_loop and schedules["counted"].max_iterations == 3
    assert schedules["dated"].until == _utc(2026, 12, 31)


def test_loop_rejects_nonpositive_max_iterations():
    with pytest.raises(ScheduleConfigError, match="at least 1"):
        parse_config(_config(schedules={}, loops={"x": {"launcher": "audit", "every": "5m", "max_iterations": 0}}))


def test_schedule_and_loop_names_must_not_collide():
    with pytest.raises(ScheduleConfigError, match="both a schedule and a loop"):
        parse_config(_config(
            schedules={"dup": {"launcher": "audit", "every": "1h"}},
            loops={"dup": {"launcher": "audit", "every": "1h", "max_iterations": 2}},
        ))


def test_parse_timestamp_treats_naive_as_utc():
    assert parse_timestamp("2026-12-31T00:00:00", "t") == _utc(2026, 12, 31)
    assert parse_timestamp("2026-12-31T00:00:00Z", "t") == _utc(2026, 12, 31)
    with pytest.raises(ScheduleConfigError):
        parse_timestamp("next tuesday", "t")


# ── due calculation ───────────────────────────────────────────────────────────

def _one(**overrides):
    spec = {"launcher": "audit", "every": "1h"}
    spec.update(overrides)
    _, schedules = parse_config(_config(schedules={"s": spec}))
    return schedules["s"]


def test_interval_schedule_fires_immediately_with_no_history():
    # "every 30m" on a freshly enabled schedule should run now, not in 30m.
    assert is_due(_one(every="30m"), None, _utc(2026, 7, 1, 12)) is True


def test_interval_schedule_waits_for_the_interval():
    state = ScheduleState(name="s", last_run_at=_utc(2026, 7, 1, 12), iterations=1)
    assert is_due(_one(every="1h"), state, _utc(2026, 7, 1, 12, 30)) is False
    assert is_due(_one(every="1h"), state, _utc(2026, 7, 1, 13, 0)) is True


def test_disabled_schedule_never_fires():
    assert next_run_at(_one(enabled=False), None, _utc(2026, 7, 1)) is None
    assert is_due(_one(enabled=False), None, _utc(2026, 7, 1)) is False


def test_loop_stops_after_max_iterations():
    loop = parse_config(_config(schedules={}, loops={
        "l": {"launcher": "audit", "every": "1m", "max_iterations": 2}
    }))[1]["l"]
    state = ScheduleState(name="l", iterations=2, last_run_at=_utc(2026, 7, 1, 12))
    now = _utc(2026, 7, 1, 13)
    assert exhaustion_reason(loop, state, now) == "completed all 2 iterations"
    assert next_run_at(loop, state, now) is None
    assert is_due(loop, state, now) is False


def test_loop_stops_after_until():
    loop = parse_config(_config(schedules={}, loops={
        "l": {"launcher": "audit", "every": "1m", "until": "2026-07-01T12:00:00Z"}
    }))[1]["l"]
    state = ScheduleState(name="l", iterations=1, last_run_at=_utc(2026, 7, 1, 11, 59))
    assert is_due(loop, state, _utc(2026, 7, 1, 11, 59, )) is False  # not yet due
    assert next_run_at(loop, state, _utc(2026, 7, 1, 13)) is None    # past until


def test_due_schedules_returns_only_due_in_name_order():
    _, schedules = parse_config(_config(schedules={
        "zeta": {"launcher": "audit", "every": "1m"},
        "alpha": {"launcher": "audit", "every": "1m"},
        "later": {"launcher": "audit", "cron": "0 3 * * *"},
    }))
    now = _utc(2026, 7, 1, 12)
    due = due_schedules(schedules.values(), {}, now)
    assert [s.name for s in due] == ["alpha", "zeta"]


def test_schedule_state_round_trips():
    state = ScheduleState(
        name="s", iterations=4, last_run_at=_utc(2026, 7, 1, 12), last_job_ids=["a", "b"]
    )
    restored = ScheduleState.from_dict(state.to_dict())
    assert restored.iterations == 4
    assert restored.last_run_at == state.last_run_at
    assert restored.last_job_ids == ["a", "b"]


def test_format_duration_is_human_readable():
    assert format_duration(30) == "30s"
    assert format_duration(5400) == "1h 30m"
    assert format_duration(86400) == "1d"


# ── condition-based loop stops (until_empty) ──────────────────────────────────

def test_until_empty_requires_a_hard_iteration_cap():
    # A queue that never drains would otherwise loop forever - the condition is
    # a stop *hint*, max_iterations is the guarantee.
    with pytest.raises(ScheduleConfigError, match="also needs 'max_iterations'"):
        parse_config(_config(schedules={}, loops={
            "drain": {"launcher": "audit", "every": "5m", "until_empty": "codex"}
        }))


def test_until_empty_is_accepted_with_a_cap():
    _, schedules = parse_config(_config(schedules={}, loops={
        "drain": {
            "launcher": "audit", "every": "5m",
            "until_empty": "codex", "max_iterations": 20,
        }
    }))
    assert schedules["drain"].until_empty == "codex"
    assert "codex queue empty" in schedules["drain"].describe_bound()


def test_until_empty_is_rejected_on_a_plain_schedule():
    with pytest.raises(ScheduleConfigError, match="only applies to loops"):
        parse_config(_config(schedules={
            "s": {"launcher": "audit", "every": "5m", "until_empty": "codex"}
        }))


def test_a_recorded_completion_is_sticky():
    # A loop that ended because its queue drained must not resurrect when the
    # queue refills; only `mco schedule reset` clears it.
    schedule = _one(every="1m")
    state = ScheduleState(name="s", iterations=1, exhausted_reason="codex queue is empty")
    now = _utc(2026, 7, 1, 12)
    assert exhaustion_reason(schedule, state, now) == "codex queue is empty"
    assert next_run_at(schedule, state, now) is None
    assert is_due(schedule, state, now) is False


# ── app / url launchers (local GUI actions) ───────────────────────────────────

def test_url_launcher_parses_and_describes():
    launchers, _ = parse_config(_config(launchers={
        "console": {"url": "http://127.0.0.1:18789/console"},
        "audit": {"role": "reviewer", "title": "t"},
    }))
    assert launchers["console"].is_local is True
    assert launchers["console"].describe().startswith("open http://")


def test_app_launcher_parses_with_args():
    launchers, _ = parse_config(_config(launchers={
        "editor": {"app": "code", "args": ["--new-window", "."]},
        "audit": {"role": "reviewer", "title": "t"},
    }))
    launcher = launchers["editor"]
    assert launcher.app == "code" and launcher.args == ("--new-window", ".")
    assert launcher.is_local is True
    assert "run code" in launcher.describe()


def test_launcher_rejects_two_target_kinds():
    with pytest.raises(ScheduleConfigError, match="exactly one of"):
        parse_config(_config(launchers={
            "x": {"role": "codex", "title": "t", "app": "notepad"},
        }))


@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "custom-handler://do-a-thing",
    "not-a-url",
])
def test_url_launcher_rejects_dangerous_schemes(bad_url):
    # A scheduler executing a config file must not hand arbitrary schemes to a
    # browser.
    with pytest.raises(ScheduleConfigError, match="must start with"):
        parse_config(_config(launchers={"x": {"url": bad_url}}))


def test_url_launcher_accepts_safe_schemes():
    for good in ("http://x.test/a", "https://x.test/a", "file:///tmp/report.html"):
        launchers, _ = parse_config(_config(launchers={
            "x": {"url": good}, "audit": {"role": "r", "title": "t"},
        }))
        assert launchers["x"].url == good


def test_args_requires_an_app_launcher():
    with pytest.raises(ScheduleConfigError, match="only applies to an 'app'"):
        parse_config(_config(launchers={
            "x": {"url": "http://x.test", "args": ["--nope"]},
        }))


def test_args_must_be_a_list():
    with pytest.raises(ScheduleConfigError, match="must be a list"):
        parse_config(_config(launchers={"x": {"app": "code", "args": "--new-window"}}))


def test_job_launcher_is_not_local():
    launchers, _ = parse_config(_config())
    assert launchers["audit"].is_local is False

import pytest

from grader.scoring import (
    GradingError,
    aggregate_passes,
    compute_results,
    extract_json,
    fmt_num,
    parse_content_tasks,
    parse_format_checks,
)


def task(name, mx, aw, verdict="partial"):
    return {"task": name, "title": "", "max_points": mx, "awarded": aw, "verdict": verdict, "feedback": ""}


def test_fmt_num():
    assert fmt_num(12.0) == "12"
    assert fmt_num(12.5) == "12.5"
    assert fmt_num(12.345) == "12.35"
    assert fmt_num(0) == "0"


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go:\n{"a": 1}\nHope that helps', '```\n{"a": 1}\n```'],
)
def test_extract_json_tolerates_fences_and_chatter(text):
    assert extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "{broken"])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_format_checks_normalise_status_and_penalty():
    obj = {
        "checks": [
            {"rule": "A", "status": "FAIL", "evidence": "x", "penalty_points": 4},
            {"rule": "B", "status": "fail", "evidence": "x"},  # no penalty given -> default
            {"rule": "C", "status": "pass", "evidence": "x", "penalty_points": 9},  # pass never costs points
            {"rule": "D", "status": "not_checkable", "evidence": "x"},
            {"rule": "E", "status": "fail", "evidence": "x", "penalty_points": -3},  # negative -> default
        ]
    }
    checks = parse_format_checks(obj, default_penalty=2)
    assert [c["penalty"] for c in checks] == [4, 2, 0, 0, 2]
    assert [c["status"] for c in checks] == ["fail", "fail", "pass", "not_checkable", "fail"]


@pytest.mark.parametrize(
    "obj",
    [[], {}, {"checks": []}, {"checks": ["x"]}, {"checks": [{"rule": "A", "status": "maybe"}]}],
)
def test_format_checks_reject_bad_shapes(obj):
    with pytest.raises(ValueError):
        parse_format_checks(obj, 2)


def test_content_tasks_clamp_awarded_to_max():
    parsed = parse_content_tasks({"tasks": [{"task": "T1", "max_points": 10, "awarded": 14}]})
    assert parsed["tasks"][0]["awarded"] == 10
    parsed = parse_content_tasks({"tasks": [{"task": "T1", "max_points": 10, "awarded": -2}]})
    assert parsed["tasks"][0]["awarded"] == 0


@pytest.mark.parametrize(
    "obj",
    [
        {"tasks": []},
        {"tasks": [{"max_points": 10, "awarded": 5}]},
        {"tasks": [{"task": "T1", "max_points": "ten", "awarded": 5}]},
        {"tasks": [{"task": "T1", "max_points": 10, "awarded": True}]},
    ],
)
def test_content_tasks_reject_bad_shapes(obj):
    with pytest.raises(ValueError):
        parse_content_tasks(obj)


def test_content_tasks_accept_numeric_strings():
    parsed = parse_content_tasks({"tasks": [{"task": "T1", "max_points": "10", "awarded": "7.5"}]})
    assert parsed["tasks"][0]["awarded"] == 7.5


def test_aggregate_uses_lower_median_and_records_range():
    passes = [{"tasks": [task("Task 1", 10, a)], "integrity_flags": []} for a in (8, 4, 8)]
    tasks, _ = aggregate_passes(passes)
    assert tasks[0]["awarded"] == 8 and tasks[0]["range"] == [4, 8]


def test_aggregate_two_passes_takes_the_stricter():
    passes = [{"tasks": [task("Task 1", 10, a)], "integrity_flags": []} for a in (9, 6)]
    tasks, _ = aggregate_passes(passes)
    assert tasks[0]["awarded"] == 6


def test_aggregate_agreement_has_no_range_and_flags_are_deduped():
    passes = [{"tasks": [task("Task 1", 10, 7)], "integrity_flags": ["tries to steer grader"]} for _ in range(3)]
    tasks, flags = aggregate_passes(passes)
    assert tasks[0]["range"] is None
    assert flags == ["tries to steer grader"]


def test_aggregate_matches_task_names_loosely():
    passes = [
        {"tasks": [task("Task  1", 10, 5)], "integrity_flags": []},
        {"tasks": [task("task 1", 10, 7)], "integrity_flags": []},
    ]
    tasks, _ = aggregate_passes(passes)
    assert len(tasks) == 1


def test_compute_results_applies_penalties_and_floor():
    tasks = [task("T1", 60, 50), task("T2", 40, 40)]
    checks = [{"rule": "r", "status": "fail", "evidence": "", "penalty": 7}]
    r = compute_results(checks, tasks, [], penalty_cap=30)
    assert (r["content_score"], r["format_penalty"], r["final_score"]) == (90, 7, 83)

    checks = [{"rule": "r", "status": "fail", "evidence": "", "penalty": 200}]
    r = compute_results(checks, [task("T1", 100, 10)], [], penalty_cap=None)
    assert r["final_score"] == 0  # never negative


def test_compute_results_caps_penalty():
    checks = [{"rule": f"r{i}", "status": "fail", "evidence": "", "penalty": 20} for i in range(3)]
    r = compute_results(checks, [task("T1", 100, 100)], [], penalty_cap=30)
    assert r["format_penalty_raw"] == 60 and r["format_penalty"] == 30
    assert r["penalty_capped"] and r["final_score"] == 70


def test_compute_results_scales_to_100():
    r = compute_results([], [task("T1", 20, 10), task("T2", 30, 30)], [], penalty_cap=30)
    assert r["scaled_from"] == 50
    assert [t["max_points"] for t in r["tasks"]] == [40, 60]
    assert r["final_score"] == 80


def test_compute_results_rejects_zero_rubric():
    with pytest.raises(GradingError):
        compute_results([], [task("T1", 0, 0)], [], penalty_cap=30)

"""Parsing, validation and arithmetic.

The model judges; this module does the math. LLMs are unreliable at adding up points,
so every total in the final report comes from code.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


class GradingError(Exception):
    """Raised when a grade cannot be produced (bad model output, empty rubric, ...)."""


def fmt_num(x: float) -> str:
    """12.0 -> '12', 12.5 -> '12.5', 12.345 -> '12.35'."""
    x = round(float(x), 2)
    if x == int(x):
        return str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def extract_json(text: str) -> Any:
    """Parse the first JSON object in a model reply, tolerating code fences and chatter."""
    s = (text or "").strip()
    if not s:
        raise ValueError("the reply was empty")
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError("no JSON object found in the reply")


def _num(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} is not a number: {value!r}")
    try:
        number = float(value if isinstance(value, (int, float)) else str(value).strip())
    except ValueError:
        raise ValueError(f"{name} is not a number: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} is not a finite number")
    return number


def parse_format_checks(obj: Any, default_penalty: float) -> list[dict]:
    """Validate the Prompt 1 reply and normalise it."""
    if not isinstance(obj, dict) or not isinstance(obj.get("checks"), list) or not obj["checks"]:
        raise ValueError("expected a non-empty 'checks' list")
    checks = []
    for item in obj["checks"]:
        if not isinstance(item, dict):
            raise ValueError("a check is not an object")
        raw_status = str(item.get("status", "")).strip().lower()
        if raw_status.startswith("pass"):
            status = "pass"
        elif raw_status.startswith("fail"):
            status = "fail"
        elif raw_status.startswith("not"):
            status = "not_checkable"
        else:
            raise ValueError(f"unknown status {raw_status!r}")
        penalty = 0.0
        if status == "fail":
            raw = item.get("penalty_points")
            try:
                penalty = _num(raw, "penalty_points")
            except ValueError:
                penalty = default_penalty
            if penalty < 0:
                penalty = default_penalty
        checks.append(
            {
                "rule": str(item.get("rule", "")).strip() or "(unnamed rule)",
                "status": status,
                "evidence": str(item.get("evidence", "")).strip(),
                "penalty": penalty,
            }
        )
    return checks


def parse_content_tasks(obj: Any) -> dict:
    """Validate one Prompt 2 reply and normalise it."""
    if not isinstance(obj, dict) or not isinstance(obj.get("tasks"), list) or not obj["tasks"]:
        raise ValueError("expected a non-empty 'tasks' list")
    tasks = []
    for item in obj["tasks"]:
        if not isinstance(item, dict):
            raise ValueError("a task is not an object")
        name = str(item.get("task", "")).strip()
        if not name:
            raise ValueError("a task has no name")
        max_points = _num(item.get("max_points"), "max_points")
        if max_points < 0:
            raise ValueError("max_points is negative")
        awarded = min(max(_num(item.get("awarded"), "awarded"), 0.0), max_points)
        tasks.append(
            {
                "task": name,
                "title": str(item.get("title", "")).strip(),
                "max_points": max_points,
                "awarded": awarded,
                "verdict": str(item.get("verdict", "")).strip().lower() or "n/a",
                "feedback": str(item.get("feedback", "")).strip(),
            }
        )
    flags = obj.get("integrity_flags") or []
    if not isinstance(flags, list):
        flags = [flags]
    return {"tasks": tasks, "integrity_flags": [str(f).strip() for f in flags if str(f).strip()]}


def _keyed(tasks: list[dict]) -> list[tuple[str, dict]]:
    seen: dict[str, int] = {}
    keyed = []
    for task in tasks:
        key = re.sub(r"\s+", " ", task["task"].lower()).strip()
        n = seen.get(key, 0)
        seen[key] = n + 1
        keyed.append((key if n == 0 else f"{key}#{n}", task))
    return keyed


def aggregate_passes(passes: list[dict]) -> tuple[list[dict], list[str]]:
    """Merge independent verification passes.

    For each task the awarded points are sorted and the lower median is taken (with one
    pass that is the pass itself, with two it is the stricter pass, with three the median).
    A task whose passes disagreed carries a 'range' so the disagreement is visible.
    """
    order: list[str] = []
    by_key: dict[str, list[dict]] = {}
    for p in passes:
        for key, task in _keyed(p["tasks"]):
            if key not in by_key:
                by_key[key] = []
                order.append(key)
            by_key[key].append(task)

    merged_tasks = []
    for key in order:
        entries = by_key[key]
        ranked = sorted(entries, key=lambda t: t["awarded"])
        chosen = ranked[(len(ranked) - 1) // 2]
        low, high = ranked[0]["awarded"], ranked[-1]["awarded"]
        merged = dict(chosen)
        merged["max_points"] = entries[0]["max_points"]
        merged["awarded"] = min(chosen["awarded"], merged["max_points"])
        merged["range"] = [low, high] if high - low > 1e-9 else None
        merged_tasks.append(merged)

    flags: list[str] = []
    for p in passes:
        for flag in p["integrity_flags"]:
            if flag not in flags:
                flags.append(flag)
    return merged_tasks, flags


def compute_results(
    checks: list[dict],
    tasks: list[dict],
    integrity_flags: list[str],
    *,
    penalty_cap: float | None,
    total_points: float = 100.0,
) -> dict:
    """Do all the arithmetic: scale to the total, apply penalties, floor at zero."""
    content_max = sum(t["max_points"] for t in tasks)
    if content_max <= 0:
        raise GradingError("The rubric's task points add up to zero, so no grade can be computed.")

    scaled_from = None if abs(content_max - total_points) < 1e-9 else content_max
    out_tasks = []
    for task in tasks:
        t = dict(task)
        if scaled_from is not None:
            factor = total_points / content_max
            t["max_points"] = round(task["max_points"] * factor, 2)
            t["awarded"] = round(task["awarded"] * factor, 2)
            if task.get("range"):
                t["range"] = [round(x * factor, 2) for x in task["range"]]
        out_tasks.append(t)

    content_score = round(sum(t["awarded"] for t in out_tasks), 2)
    raw_penalty = round(sum(c["penalty"] for c in checks), 2)
    penalty = raw_penalty if penalty_cap is None else min(raw_penalty, penalty_cap)
    final = round(max(0.0, content_score - penalty), 2)
    return {
        "total_points": total_points,
        "tasks": out_tasks,
        "format_checks": checks,
        "content_score": content_score,
        "format_penalty_raw": raw_penalty,
        "format_penalty": round(penalty, 2),
        "penalty_capped": penalty < raw_penalty,
        "final_score": final,
        "scaled_from": scaled_from,
        "integrity_flags": integrity_flags,
    }

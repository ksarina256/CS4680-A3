"""Deterministic report rendering and validation of the model-written report."""

from __future__ import annotations

import re

from .scoring import fmt_num


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|").strip() or "-"


def task_note(task: dict) -> str:
    rng = task.get("range")
    if not rng:
        return ""
    return f"Verification passes disagreed ({fmt_num(rng[0])} to {fmt_num(rng[1])}); the stricter middle value was used."


def render_report(results: dict) -> str:
    """Build the full report from computed results, with no model involved."""
    total = fmt_num(results["total_points"])
    lines = [
        "# Grade Report",
        "",
        "## Final Score",
        "",
        f"**{fmt_num(results['final_score'])}/{total}**",
        "",
        f"Content subtotal {fmt_num(results['content_score'])}/{total}, "
        f"formatting penalty -{fmt_num(results['format_penalty'])}.",
    ]
    if results.get("scaled_from") is not None:
        lines += ["", f"Rubric points summed to {fmt_num(results['scaled_from'])}; scores were scaled to {total}."]

    lines += ["", "## Task Breakdown", "", "| Task | Points | Verdict | Feedback |", "|---|---|---|---|"]
    for t in results["tasks"]:
        feedback = t["feedback"]
        note = task_note(t)
        if note:
            feedback = f"{feedback} ({note})"
        title = f" - {t['title']}" if t["title"] else ""
        lines.append(
            f"| {_cell(t['task'] + title)} | {fmt_num(t['awarded'])}/{fmt_num(t['max_points'])} "
            f"| {_cell(t['verdict'])} | {_cell(feedback)} |"
        )

    failed = [c for c in results["format_checks"] if c["status"] == "fail"]
    skipped = [c for c in results["format_checks"] if c["status"] == "not_checkable"]
    lines += ["", "## Formatting Penalties", ""]
    if failed:
        lines += ["| Rule | Penalty | Evidence |", "|---|---|---|"]
        for c in failed:
            lines.append(f"| {_cell(c['rule'])} | -{fmt_num(c['penalty'])} | {_cell(c['evidence'])} |")
        if results["penalty_capped"]:
            lines += [
                "",
                f"Penalties totalled {fmt_num(results['format_penalty_raw'])} and were capped at "
                f"{fmt_num(results['format_penalty'])}.",
            ]
    else:
        lines.append("No formatting penalties applied.")
    if skipped:
        lines += ["", "Not checkable: " + "; ".join(_cell(c["rule"]) for c in skipped) + "."]

    if results["integrity_flags"]:
        lines += ["", "## Integrity Flags", ""]
        lines += [f"- {_cell(f)}" for f in results["integrity_flags"]]

    lost = [t for t in results["tasks"] if t["max_points"] - t["awarded"] > 1e-9]
    summary = "Content points were lost in " + ", ".join(
        f"{t['task']} (-{fmt_num(t['max_points'] - t['awarded'])})" for t in lost
    ) + "." if lost else "No content points were lost."
    if failed:
        summary += f" Formatting violations cost {fmt_num(results['format_penalty'])} points."
    lines += ["", "## Grader's Summary", "", summary, ""]
    return "\n".join(lines)


def report_matches(report: str, results: dict) -> bool:
    """True if the model-written report carries the computed final score and every task score."""
    compact = re.sub(r"\s*/\s*", "/", report)
    total = fmt_num(results["total_points"])
    if f"{fmt_num(results['final_score'])}/{total}" not in compact:
        return False
    return all(f"{fmt_num(t['awarded'])}/{fmt_num(t['max_points'])}" in compact for t in results["tasks"])

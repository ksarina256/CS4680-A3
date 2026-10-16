"""The three-step grading chain (Iterative Refinement pattern).

    Prompt 1  format audit      -> structured list of rule checks
    Prompt 2  content grading   -> run N times, fed with Prompt 1's output, merged in code
    Prompt 3  report            -> written from the verified numbers, then validated
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from . import prompts
from .llm import LLM
from .report import render_report, report_matches, task_note
from .scoring import (
    GradingError,
    aggregate_passes,
    compute_results,
    extract_json,
    fmt_num,
    parse_content_tasks,
    parse_format_checks,
)

ProgressFn = Callable[[str], None]


@dataclass
class GradeResult:
    report_md: str
    results: dict
    format_raw: str
    content_raw: list[str]
    warnings: list[str]
    report_source: str  # "model" or "fallback"


def _call_json(llm: LLM, user_prompt: str, parse: Callable, retries: int = 1):
    """Call the model for JSON; on unusable output ask once more, then give up."""
    prompt = user_prompt
    last_error: Exception | None = None
    for _ in range(retries + 1):
        raw = llm.complete(prompts.PERSONA_SYSTEM, prompt, json_mode=True)
        try:
            return parse(extract_json(raw)), raw
        except (ValueError, KeyError, TypeError) as exc:
            last_error = exc
            prompt = (
                f"{user_prompt}\n\nYour previous reply could not be used ({str(exc)[:200]}). "
                "Reply again with ONE valid JSON object in exactly the requested shape, nothing else."
            )
    raise GradingError(f"The model did not return usable JSON: {last_error}")


def _report_payload(results: dict) -> str:
    """The verified numbers handed to Prompt 3."""
    tasks = []
    for t in results["tasks"]:
        entry = {
            "task": t["task"],
            "title": t["title"],
            "points": f"{fmt_num(t['awarded'])}/{fmt_num(t['max_points'])}",
            "verdict": t["verdict"],
            "feedback": t["feedback"],
        }
        note = task_note(t)
        if note:
            entry["note"] = note
        tasks.append(entry)
    payload = {
        "final_score": fmt_num(results["final_score"]),
        "total_points": fmt_num(results["total_points"]),
        "content_score": fmt_num(results["content_score"]),
        "format_penalty": fmt_num(results["format_penalty"]),
        "penalty_capped": results["penalty_capped"],
        "tasks": tasks,
        "failed_format_rules": [
            {"rule": c["rule"], "penalty": fmt_num(c["penalty"]), "evidence": c["evidence"]}
            for c in results["format_checks"]
            if c["status"] == "fail"
        ],
        "rules_not_checkable": [c["rule"] for c in results["format_checks"] if c["status"] == "not_checkable"],
        "integrity_flags": results["integrity_flags"],
    }
    return json.dumps(payload, indent=2)


def grade_submission(
    llm: LLM,
    rubric: str,
    rules: str,
    submission: str,
    filenames: str | Sequence[str] = "",
    *,
    passes: int = 3,
    default_penalty: float = 2.0,
    penalty_cap: Optional[float] = 30.0,
    total_points: float = 100.0,
    progress: Optional[ProgressFn] = None,
) -> GradeResult:
    if not rubric.strip():
        raise ValueError("The rubric is empty.")
    if not submission.strip():
        raise ValueError("The student submission is empty.")
    passes = max(1, min(int(passes), 5))
    say = progress or (lambda _msg: None)
    warnings: list[str] = []
    names = filenames if isinstance(filenames, str) else "\n".join(filenames)

    # Prompt 1: format audit
    if rules.strip():
        say("Step 1 of 3: auditing the submission against the formatting rules")
        checks, format_raw = _call_json(
            llm,
            prompts.format_audit_prompt(rules, submission, names, default_penalty),
            lambda obj: parse_format_checks(obj, default_penalty),
        )
    else:
        checks, format_raw = [], ""
        warnings.append("No formatting rules were provided, so the format audit was skipped.")

    # Prompt 2: content grading, repeated for independent verification passes
    audit_json = json.dumps(checks, indent=2) if checks else "[]"
    content_prompt = prompts.content_eval_prompt(rubric, submission, audit_json)
    parsed_passes: list[dict] = []
    content_raw: list[str] = []
    for i in range(passes):
        say(f"Step 2 of 3: grading content against the rubric (verification pass {i + 1} of {passes})")
        parsed, raw = _call_json(llm, content_prompt, parse_content_tasks)
        parsed_passes.append(parsed)
        content_raw.append(raw)

    tasks, flags = aggregate_passes(parsed_passes)
    results = compute_results(checks, tasks, flags, penalty_cap=penalty_cap, total_points=total_points)
    if results["scaled_from"] is not None:
        warnings.append(
            f"The rubric's points add up to {fmt_num(results['scaled_from'])}, not {fmt_num(total_points)}; "
            "scores were scaled proportionally."
        )
    disagreed = [t["task"] for t in results["tasks"] if t.get("range")]
    if disagreed:
        warnings.append(
            "Verification passes disagreed on: " + ", ".join(disagreed) + ". The stricter middle value was used."
        )
    if results["integrity_flags"]:
        warnings.append("The submission appears to contain instructions aimed at the grader. See Integrity Flags.")

    # Prompt 3: compile the report, then check the model kept our numbers
    say("Step 3 of 3: compiling the final report")
    report_md, source = "", "fallback"
    try:
        candidate = llm.complete(prompts.PERSONA_SYSTEM, prompts.report_prompt(_report_payload(results)))
        if report_matches(candidate, results):
            report_md, source = candidate.strip(), "model"
        else:
            warnings.append("The model's report did not match the computed scores, so a code-generated report is shown.")
    except Exception:  # noqa: BLE001 - the scores are already computed; don't lose them
        warnings.append("The report step failed, so a code-generated report is shown.")
    if not report_md:
        report_md = render_report(results)

    return GradeResult(
        report_md=report_md,
        results=results,
        format_raw=format_raw,
        content_raw=content_raw,
        warnings=warnings,
        report_source=source,
    )

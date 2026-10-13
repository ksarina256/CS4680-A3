"""A scripted stand-in for the LLM, so the pipeline can be tested without an API key."""

from __future__ import annotations

import json
import re

TASK_MAX = [10, 15, 15, 15, 15, 15, 15]


def format_reply(failed: dict[int, float] | None = None, evidence: str = "File name is ana_lee_assignment2.md") -> str:
    """Six rules; `failed` maps rule index -> penalty."""
    failed = failed if failed is not None else {0: 5, 3: 4, 4: 3}
    checks = []
    for i in range(6):
        if i in failed:
            checks.append({"rule": f"Rule {i + 1}", "status": "fail", "evidence": evidence, "penalty_points": failed[i]})
        else:
            checks.append({"rule": f"Rule {i + 1}", "status": "pass", "evidence": "ok", "penalty_points": 0})
    return json.dumps({"checks": checks})


def content_reply(awards: list[float], flags: list[str] | None = None, maxes: list[float] | None = None) -> str:
    maxes = maxes or TASK_MAX
    tasks = [
        {
            "task": f"Task {i + 1}",
            "title": f"Title {i + 1}",
            "max_points": m,
            "awarded": a,
            "verdict": "correct" if a == m else "partial",
            "feedback": f"Feedback {i + 1}",
        }
        for i, (m, a) in enumerate(zip(maxes, awards))
    ]
    return json.dumps({"tasks": tasks, "integrity_flags": flags or []})


def faithful_report(user_prompt: str) -> str:
    """What a well-behaved model would write: copies the verified numbers exactly."""
    payload = json.loads(re.search(r"<verified_results>\s*(.*?)\s*</verified_results>", user_prompt, re.S).group(1))
    lines = ["# Grade Report", "## Final Score", f"**{payload['final_score']}/{payload['total_points']}**", "## Task Breakdown"]
    lines += [f"| {t['task']} | {t['points']} | {t['verdict']} | {t['feedback']} |" for t in payload["tasks"]]
    return "\n".join(lines)


def unfaithful_report(user_prompt: str) -> str:
    return "# Grade Report\n## Final Score\n**99/100**\nGreat work!"


class FakeLLM:
    def __init__(self, format_replies, content_replies, report=faithful_report):
        self.format_replies = list(format_replies)
        self.content_replies = list(content_replies)
        self.report = report
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if "TASK: FORMAT AUDIT" in user:
            return self.format_replies.pop(0)
        if "TASK: CONTENT EVALUATION" in user:
            return self.content_replies.pop(0)
        if "TASK: FINAL REPORT" in user:
            if isinstance(self.report, Exception):
                raise self.report
            return self.report(user)
        raise AssertionError("unexpected prompt")

    def steps(self) -> list[str]:
        names = {"FORMAT AUDIT": "format", "CONTENT EVALUATION": "content", "FINAL REPORT": "report"}
        return [next(v for k, v in names.items() if k in c["user"]) for c in self.calls]


# Three passes that disagree on tasks 3 and 7; the lower median gives 10 and 10.
DISAGREEING_PASSES = [
    content_reply([10, 15, 10, 15, 15, 15, 10]),
    content_reply([10, 15, 5, 15, 15, 15, 10]),
    content_reply([10, 15, 10, 15, 15, 15, 5]),
]
# Content = 90, format penalty = 5 + 4 + 3 = 12, final = 78.
EXPECTED_FINAL = 78

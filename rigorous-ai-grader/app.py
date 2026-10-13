"""Streamlit UI for the Rigorous AI Grader."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from grader import llm as llm_mod  # module import, so make_llm can be swapped in tests
from grader.pipeline import grade_submission
from grader.scoring import GradingError, fmt_num

SAMPLES = Path(__file__).parent / "samples"

st.set_page_config(page_title="Rigorous AI Grader", page_icon="📝", layout="wide")


def _read_sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def _secret(name: str) -> str:
    """API key from Streamlit secrets or the environment, if configured on the server."""
    try:
        value = st.secrets.get(name)
    except Exception:  # noqa: BLE001 - no secrets file configured
        value = None
    return str(value or os.environ.get(name) or "")


def load_sample() -> None:
    st.session_state.update(
        rubric=_read_sample("rubric.md"),
        rules=_read_sample("formatting_rules.md"),
        submission=_read_sample("submission.md"),
        filename=_read_sample("filename.txt").strip(),
    )
    st.session_state.pop("result", None)


def clear_all() -> None:
    st.session_state.update(rubric="", rules="", submission="", filename="")
    st.session_state.pop("result", None)


for field in ("rubric", "rules", "submission", "filename"):
    st.session_state.setdefault(field, "")

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    provider = st.selectbox("AI provider", list(llm_mod.PROVIDERS), key="provider")
    env_name = llm_mod.PROVIDERS[provider]["env"]
    server_key = _secret(env_name)
    typed_key = st.text_input(
        f"{provider} API key",
        type="password",
        placeholder="Using the key configured on the server" if server_key else "Paste your API key",
        help=f"Stored only for this session. You can also set {env_name} as a secret or environment variable.",
    )
    model = st.text_input("Model", value=llm_mod.default_model(provider), key=f"model_{provider}")
    passes = st.slider(
        "Verification passes",
        1,
        3,
        3,
        help="Content grading runs this many independent times. The stricter middle score is kept "
        "and any disagreement is flagged. More passes means a slower, more reliable grade.",
    )
    default_penalty = st.number_input(
        "Default penalty per failed rule (points)",
        min_value=0.0,
        max_value=20.0,
        value=2.0,
        step=0.5,
        help="Used when a formatting rule does not state its own penalty.",
    )
    penalty_cap = st.number_input(
        "Maximum total formatting penalty (points)",
        min_value=0.0,
        max_value=100.0,
        value=30.0,
        step=1.0,
    )

# ---------------------------------------------------------------- inputs
st.title("Rigorous AI Grader")
st.caption("Paste a rubric, the formatting rules and a submission. Get a strict, itemized grade out of 100.")

col_rubric, col_rules, col_submission = st.columns(3)
col_rubric.text_area(
    "Assignment rubric",
    key="rubric",
    height=380,
    placeholder="List each task with its points and criteria. Points should add up to 100.",
)
col_rules.text_area(
    "Formatting and validation rules",
    key="rules",
    height=380,
    placeholder="One rule per line, with a penalty if you want one, for example:\n"
    "1. File name must be LastName_FirstName_A2.md. Penalty: 5 points.",
)
col_submission.text_area(
    "Student submission",
    key="submission",
    height=380,
    placeholder="Paste the student's full submission text here.",
)
st.text_input(
    "Submission file name (optional, needed to check file-naming rules)",
    key="filename",
    placeholder="e.g. Lee_Ana_A2.md",
)

btn_grade, btn_sample, btn_clear = st.columns([3, 3, 1])
grade_clicked = btn_grade.button("Grade Submission", type="primary", key="grade", width="stretch")
btn_sample.button("Load sample assignment", key="sample", on_click=load_sample, width="stretch")
btn_clear.button("Clear", key="clear", on_click=clear_all, width="stretch")

# ---------------------------------------------------------------- grading
if grade_clicked:
    api_key = typed_key.strip() or server_key
    if not api_key:
        st.error(f"Add a {provider} API key in the sidebar first.")
    elif not st.session_state.rubric.strip() or not st.session_state.submission.strip():
        st.error("The rubric and the student submission are both required.")
    else:
        try:
            client = llm_mod.make_llm(provider, api_key, model.strip())
            with st.status("Grading in progress", expanded=True) as status:
                outcome = grade_submission(
                    client,
                    st.session_state.rubric,
                    st.session_state.rules,
                    st.session_state.submission,
                    st.session_state.filename,
                    passes=passes,
                    default_penalty=default_penalty,
                    penalty_cap=penalty_cap,
                    progress=st.write,
                )
                status.update(label="Grading complete", state="complete", expanded=False)
            st.session_state["result"] = outcome
        except (GradingError, ValueError) as exc:
            st.session_state.pop("result", None)
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface provider errors (bad key, quota, ...)
            st.session_state.pop("result", None)
            st.error(f"The AI provider returned an error: {exc}")

# ---------------------------------------------------------------- output
outcome = st.session_state.get("result")
if outcome:
    results = outcome.results
    total = fmt_num(results["total_points"])
    st.divider()
    m_final, m_content, m_penalty = st.columns(3)
    m_final.metric("Final score", f"{fmt_num(results['final_score'])} / {total}")
    m_content.metric("Content score", f"{fmt_num(results['content_score'])} / {total}")
    m_penalty.metric("Formatting penalties", f"-{fmt_num(results['format_penalty'])}")

    for warning in outcome.warnings:
        st.warning(warning)

    st.markdown(outcome.report_md)
    st.download_button(
        "Download report (.md)",
        outcome.report_md,
        file_name="grade_report.md",
        mime="text/markdown",
        key="download",
    )

    with st.expander("How this grade was produced (the prompt chain)"):
        st.caption(
            "All point totals are calculated in code, not by the model. "
            f"The final report was {'written by the model and checked against the computed scores' if outcome.report_source == 'model' else 'generated by code'}."
        )
        if outcome.format_raw:
            st.markdown("**Prompt 1: format audit**")
            st.code(outcome.format_raw, language="json")
        for i, raw in enumerate(outcome.content_raw, start=1):
            st.markdown(f"**Prompt 2: content grading, verification pass {i}**")
            st.code(raw, language="json")

st.caption(
    "AI-generated grades can be wrong. A human should review every grade before it is released. "
    "Do not paste real student data into a public deployment."
)

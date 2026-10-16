"""Streamlit UI for the Rigorous AI Grader."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from grader import files as files_mod
from grader import llm as llm_mod  # module import, so make_llm can be swapped in tests
from grader.pipeline import grade_submission
from grader.scoring import GradingError, fmt_num

SAMPLES = Path(__file__).parent / "samples"

st.set_page_config(page_title="Rigorous AI Grader", page_icon="📝", layout="wide")


def _read_sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


@st.cache_data(show_spinner=False, max_entries=8)
def _load_uploads(uploads: tuple[tuple[str, bytes], ...]) -> files_mod.LoadResult:
    """Parse uploaded files once, not on every widget interaction."""
    return files_mod.load_uploads(uploads)


def _size(num_bytes: int) -> str:
    return f"{num_bytes / 1024:.1f} KB" if num_bytes >= 1024 else f"{num_bytes} B"


def _status(f: files_mod.LoadedFile) -> str:
    if not f.readable:
        return f"Not read: {f.note}"
    if f.truncated:
        return f"Read, cut ({f.note})"
    if not f.text.strip():
        return "Empty"
    return "Read"


def _new_uploader() -> None:
    """File uploaders can't be reset directly; a new key gives a fresh, empty one."""
    st.session_state["uploader_nonce"] = st.session_state.get("uploader_nonce", 0) + 1


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
    _new_uploader()  # the sample is pasted text, so drop any uploaded files
    st.session_state.pop("result", None)


def clear_all() -> None:
    st.session_state.update(rubric="", rules="", submission="", filename="")
    _new_uploader()
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
st.caption(
    "Paste a rubric and the formatting rules, then upload the student's files (or paste the text). "
    "Get a strict, itemized grade out of 100."
)

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
with col_submission:
    uploaded = st.file_uploader(
        "Student submission files",
        accept_multiple_files=True,
        key=f"uploads_{st.session_state.get('uploader_nonce', 0)}",
        help="Select all the files at once, or upload one .zip. Code, text, Markdown, notebooks, "
        "PDFs with selectable text and Word .docx files are read. Images and other binary files are "
        "listed by name only.",
    )
    st.text_area(
        "Or paste the submission text",
        key="submission",
        height=170,
        placeholder="Paste the student's full submission text here. It is added to any uploaded files.",
    )
st.text_input(
    "File name for the pasted text (optional, needed to check file-naming rules)",
    key="filename",
    placeholder="e.g. Lee_Ana_A2.md. Uploaded files are checked by their own names.",
)

loaded = _load_uploads(tuple((u.name, u.getvalue()) for u in uploaded)) if uploaded else files_mod.LoadResult()

# The buttons come before the file list so they stay near the inputs, however many files are uploaded.
btn_grade, btn_sample, btn_clear = st.columns([3, 3, 1])
grade_clicked = btn_grade.button("Grade Submission", type="primary", key="grade", width="stretch")
btn_sample.button("Load sample assignment", key="sample", on_click=load_sample, width="stretch")
btn_clear.button("Clear", key="clear", on_click=clear_all, width="stretch")

if loaded.files:
    readable = sum(f.readable for f in loaded.files)
    with st.expander(f"Files received: {len(loaded.files)} ({readable} readable)", expanded=len(loaded.files) <= 8):
        st.dataframe(
            [
                {
                    "File": f.name,
                    "Type": files_mod.KIND_LABELS.get(f.kind, f.kind),
                    "Size": _size(f.size),
                    "Status": _status(f),
                }
                for f in loaded.files
            ],
            hide_index=True,
            width="stretch",
            column_config={"Status": st.column_config.TextColumn("Status", width="large")},
        )
    for warning in loaded.warnings:
        st.warning(warning)

if loaded.files or st.session_state.submission.strip():
    try:
        preview = files_mod.build_submission(loaded, st.session_state.submission, st.session_state.filename)
        ai_calls = passes + (1 if st.session_state.rules.strip() else 0) + 1
        st.caption(
            f"Each AI call will include {preview.total_chars:,} characters of submission "
            f"(roughly {preview.total_chars // 4:,} tokens). {passes} verification pass(es) mean {ai_calls} AI calls."
        )
    except ValueError as exc:
        st.error(str(exc))


# ---------------------------------------------------------------- grading
if grade_clicked:
    api_key = typed_key.strip() or server_key
    if not api_key:
        st.error(f"Add a {provider} API key in the sidebar first.")
    elif not st.session_state.rubric.strip():
        st.error("The rubric is required.")
    else:
        try:
            submission = files_mod.build_submission(loaded, st.session_state.submission, st.session_state.filename)
            client = llm_mod.make_llm(provider, api_key, model.strip())
            with st.status("Grading in progress", expanded=True) as status:
                outcome = grade_submission(
                    client,
                    st.session_state.rubric,
                    st.session_state.rules,
                    submission.text,
                    submission.manifest,
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
    "Do not upload or paste real student data into a public deployment."
)

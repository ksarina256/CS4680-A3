from pathlib import Path

from streamlit.testing.v1 import AppTest

from fakes import DISAGREEING_PASSES, EXPECTED_FINAL, FakeLLM, format_reply

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def fresh_app():
    return AppTest.from_file(APP, default_timeout=30)


def test_sample_loads_and_grades_end_to_end(monkeypatch):
    fake = FakeLLM([format_reply()], list(DISAGREEING_PASSES))
    monkeypatch.setattr("grader.llm.make_llm", lambda provider, key, model: fake)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    at = fresh_app()
    at.run()
    assert not at.exception

    at.button(key="sample").click().run()
    assert "Task 1" in at.text_area(key="rubric").value
    assert at.text_input(key="filename").value == "ana_lee_assignment2.md"

    at.button(key="grade").click().run()
    assert not at.exception and not at.error
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Final score"] == f"{EXPECTED_FINAL} / 100"
    assert metrics["Formatting penalties"] == "-12"
    assert any("Grade Report" in md.value for md in at.markdown)
    assert fake.steps() == ["format", "content", "content", "content", "report"]


def upload(at, files, nonce=0):
    at.file_uploader(key=f"uploads_{nonce}").set_value(files)
    at.run()


def test_twelve_uploaded_files_are_graded_together(monkeypatch):
    fake = FakeLLM([format_reply()], list(DISAGREEING_PASSES))
    monkeypatch.setattr("grader.llm.make_llm", lambda provider, key, model: fake)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    at = fresh_app()
    at.run()
    at.text_area(key="rubric").set_value("Task 1 ... Task 7 (100 points)")
    at.text_area(key="rules").set_value("1. File names must be taskN.py.")
    upload(at, [(f"task{i}.py", f"print({i})".encode(), "text/x-python") for i in range(1, 13)])
    assert not at.exception

    table = at.dataframe[0].value
    assert len(table) == 12 and set(table["Status"]) == {"Read"}
    assert any("characters of submission" in c.value for c in at.caption)

    at.button(key="grade").click().run()
    assert not at.exception and not at.error
    assert {m.label: m.value for m in at.metric}["Final score"] == f"{EXPECTED_FINAL} / 100"

    format_prompt = next(c["user"] for c in fake.calls if "FORMAT AUDIT" in c["user"])
    assert "===== FILE: task1.py =====" in format_prompt and "===== FILE: task12.py =====" in format_prompt


def test_zip_upload_and_unreadable_files_are_reported(monkeypatch):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hw/main.py", "print('hi')")
        zf.writestr("hw/diagram.png", b"\x89PNG\x00\x00")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    at = fresh_app()
    at.run()
    upload(at, [("hw.zip", buf.getvalue(), "application/zip")])
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table["File"]) == ["hw/diagram.png", "hw/main.py"]
    assert any("could not be read" in w.value and "diagram.png" in w.value for w in at.warning)


def test_only_unreadable_files_cannot_be_graded(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    at = fresh_app()
    at.run()
    at.text_area(key="rubric").set_value("Task 1 (100 points)")
    upload(at, [("scan.png", b"\x89PNG\x00\x00", "image/png")])
    at.button(key="grade").click().run()
    assert not at.exception
    assert any("None of the uploaded files could be read" in e.value for e in at.error)
    assert not at.metric


def test_loading_the_sample_replaces_uploaded_files(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    at = fresh_app()
    at.run()
    upload(at, [("a.py", b"print(1)", "text/x-python")])
    assert len(at.dataframe) == 1

    at.button(key="sample").click().run()
    assert not at.exception
    assert len(at.dataframe) == 0  # the uploader was reset, so no files are listed
    assert at.file_uploader(key="uploads_1").value in (None, [])
    assert "Task 1" in at.text_area(key="rubric").value


def test_missing_api_key_shows_a_clear_error(monkeypatch):
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    at = fresh_app()
    at.run()
    at.button(key="sample").click().run()
    at.button(key="grade").click().run()
    assert not at.exception
    assert any("API key" in e.value for e in at.error)


def test_empty_inputs_show_an_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    at = fresh_app()
    at.run()
    at.button(key="grade").click().run()
    assert any("required" in e.value for e in at.error)


def test_provider_errors_are_shown_not_raised(monkeypatch):
    class Boom:
        def complete(self, *args, **kwargs):
            raise RuntimeError("401 invalid API key")

    monkeypatch.setattr("grader.llm.make_llm", lambda provider, key, model: Boom())
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    at = fresh_app()
    at.run()
    at.button(key="sample").click().run()
    at.button(key="grade").click().run()
    assert not at.exception
    assert any("invalid API key" in e.value for e in at.error)
    assert not any(m.label == "Final score" for m in at.metric)

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

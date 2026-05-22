from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_streamlit_ui_file_can_load_without_package_context() -> None:
    """Streamlit executes the UI file as a script, so imports must be absolute."""
    ui_path = Path("src/prepbuddy/ui.py")
    spec = importlib.util.spec_from_file_location("streamlit_app", ui_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert hasattr(module, "main")


def test_cli_ui_launches_streamlit_with_browser_by_default(monkeypatch) -> None:
    """The CLI defaults to Streamlit browser launch when available."""
    from prepbuddy import cli

    calls: list[list[str]] = []

    def fake_call(args: list[str]) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(cli.subprocess, "call", fake_call)

    cli.ui()

    assert calls == [
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "--server.headless=false",
            "--server.address=127.0.0.1",
            "--server.port=8501",
            "--browser.gatherUsageStats=false",
            str(Path(cli.__file__).with_name("ui.py")),
        ]
    ]


def test_cli_ui_can_launch_streamlit_headless(monkeypatch) -> None:
    """The CLI still supports manual URL opening for WSL/headless environments."""
    from prepbuddy import cli

    calls: list[list[str]] = []

    def fake_call(args: list[str]) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(cli.subprocess, "call", fake_call)

    cli.ui(open_browser=False)

    assert "--server.headless=true" in calls[0]


def test_ui_uses_current_streamlit_width_api() -> None:
    """Streamlit warns that use_container_width will be removed."""
    source = Path("src/prepbuddy/ui.py").read_text(encoding="utf-8")

    assert "use_container_width" not in source
    assert "width=\"stretch\"" in source


def test_ui_handles_answer_submission_validation_errors_inline() -> None:
    """Expected submit errors should render in the UI instead of a traceback."""
    source = Path("src/prepbuddy/ui.py").read_text(encoding="utf-8")

    assert "except ValueError as exc:" in source
    assert "st.error(str(exc))" in source


def test_ui_uses_stateful_start_session_knowledge_navigation() -> None:
    """Generation needs a stateful tab control so the UI can jump to Session."""
    source = Path("src/prepbuddy/ui.py").read_text(encoding="utf-8")

    assert "st.tabs" not in source
    assert "active_tab" in source
    assert "Start" in source
    assert "Session" in source
    assert "Knowledge" in source
    assert "st.segmented_control" in source or "st.radio" in source


def test_ui_navigation_does_not_mix_widget_state_and_default_value() -> None:
    """Streamlit warns if a keyed widget has both session-state value and default."""
    source = Path("src/prepbuddy/ui.py").read_text(encoding="utf-8")

    active_tab_source = source.split("def _active_tab", 1)[1].split("def main", 1)[0]
    assert 'key="active_tab_selector"' in active_tab_source
    assert "default=current" not in active_tab_source


def test_sidebar_removes_question_count_and_uses_pending_danger_confirmation() -> None:
    """Question count belongs in Start; destructive actions use one red confirmation button."""
    source = Path("src/prepbuddy/ui.py").read_text(encoding="utf-8")
    sidebar_source = source.split("def _render_sidebar", 1)[1].split("def _documents_table", 1)[0]

    assert "Questions per section" not in sidebar_source
    assert "Confirm document deletion" not in source
    assert "pending_danger_action" in source
    assert "Confirm selection" in source


def test_session_score_is_rendered_through_one_success_path() -> None:
    """A completed session should not show the same score twice."""
    source = Path("src/prepbuddy/ui.py").read_text(encoding="utf-8")

    assert source.count('st.success(f"Score:') == 1

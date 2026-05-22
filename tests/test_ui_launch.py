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

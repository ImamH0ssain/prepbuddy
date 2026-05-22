from __future__ import annotations

from pathlib import Path


def test_dockerignore_excludes_local_venvs_and_runtime_data() -> None:
    content = Path(".dockerignore").read_text(encoding="utf-8")

    assert "venv" in content
    assert "data/uploads" in content
    assert "data/mappings" in content


def test_compose_does_not_require_env_file_and_includes_ui_service() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "env_file:" not in content
    assert "GEMINI_API_KEY: ${GEMINI_API_KEY:-}" in content
    assert "ui:" in content
    assert "prepbuddy ui --host 0.0.0.0 --port 8501 --no-open-browser" in content
    assert '"8501:8501"' in content


def test_dockerfile_installs_runtime_ui_extras_and_uses_non_root_user() -> None:
    content = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'python -m pip install ".[ui]"' in content
    assert "USER prepbuddy" in content
    assert "EXPOSE 8000 8501" in content
    assert 'CMD ["prepbuddy", "api"' in content

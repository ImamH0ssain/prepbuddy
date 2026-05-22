from __future__ import annotations

from pathlib import Path

from prepbuddy.config_file import gemini_key_status, update_gemini_key
from prepbuddy.path_utils import managed_upload_path, safe_filename


def test_managed_upload_path_is_safe_and_stable(tmp_path: Path) -> None:
    target = managed_upload_path(tmp_path, "../My Brief (Final).pdf", b"abc")

    assert target == tmp_path / "ba7816bf_my_brief_final.pdf"
    assert safe_filename("../My Brief (Final).pdf") == "my_brief_final.pdf"


def test_update_gemini_key_preserves_unrelated_env_lines(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\nPREPBUDDY_LLM_PROVIDER=auto\nGEMINI_API_KEY=old\n", encoding="utf-8")

    update_gemini_key(env_path, "new-secret")

    assert env_path.read_text(encoding="utf-8") == (
        "# comment\nPREPBUDDY_LLM_PROVIDER=auto\nGEMINI_API_KEY=new-secret\n"
    )
    assert gemini_key_status(settings_key=None, session_key="new-secret") == "set for this session"
    assert gemini_key_status(settings_key="stored-secret", session_key=None) == "loaded from environment/.env"


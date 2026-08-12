from __future__ import annotations

import subprocess
from pathlib import Path


def test_access_token_file_not_tracked_by_git() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "outputs/zerodha/access_token.txt"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def test_gitignore_has_secret_hygiene_rules() -> None:
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")

    required_rules = ("outputs/zerodha/", "*.token", "*.secret", ".env")
    for rule in required_rules:
        assert rule in gitignore

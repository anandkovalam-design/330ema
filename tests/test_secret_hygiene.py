from __future__ import annotations

from pathlib import Path


def test_no_tracked_access_token_file() -> None:
    tracked_token = (
        Path(__file__).resolve().parents[1] / "outputs" / "zerodha" / "access_token.txt"
    )
    assert not tracked_token.exists()


def test_gitignore_has_secret_hygiene_rules() -> None:
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")

    required_rules = ("outputs/zerodha/", "*.token", "*.secret", ".env")
    for rule in required_rules:
        assert rule in gitignore

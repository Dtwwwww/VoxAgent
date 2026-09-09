from pathlib import Path


def test_verify_script_has_all_required_gates() -> None:
    script = Path(__file__).parents[3] / "scripts" / "verify.ps1"
    text = script.read_text(encoding="utf-8")
    for command in ("pytest", "ruff", "vitest", "tsc.cmd", "vite.cmd"):
        assert command in text
    assert "exit 1" in text

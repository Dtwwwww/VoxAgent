from pathlib import Path


def test_gitignore_covers_private_runtime_artifacts() -> None:
    text = (Path(__file__).parents[3] / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".token_tmp",
        "node_modules/",
        ".pytest-*/",
        "benchmarks/voice-review-*/",
        "*.db-wal",
        "*.db-shm",
    ):
        assert pattern in text

from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path

import pytest

from voxagent.tools.path_policy import PathAuthorizationError, PathPolicy


def write_file(path: Path, content: str = "safe") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def assert_denied(candidate: str, roots: tuple[Path, ...]) -> None:
    with pytest.raises(PathAuthorizationError) as error:
        PathPolicy.resolve_authorized(candidate, roots)
    message = str(error.value)
    assert "authorized" in message
    assert str(roots[0]) not in message


def test_relative_candidate_resolves_below_authorized_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = write_file(root / "docs" / "note.txt")

    resolved = PathPolicy.resolve_authorized("docs/note.txt", (root,))

    assert resolved == target.resolve(strict=True)


def test_absolute_candidate_resolves_when_inside_authorized_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = write_file(root / "note.txt")

    resolved = PathPolicy.resolve_authorized(str(target), (root,))

    assert resolved == target.resolve(strict=True)


def test_rejects_traversal_outside_authorized_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = write_file(tmp_path / "outside.txt")

    assert_denied(f"..{os.sep}{outside.name}", (root,))


def test_rejects_absolute_path_outside_authorized_roots(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = write_file(tmp_path / "outside.txt")

    assert_denied(str(outside), (root,))


@pytest.mark.parametrize("candidate", [r"\\server\share\secret.txt", r"\\?\C:\secret.txt"])
def test_rejects_unc_and_device_paths(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "root"
    root.mkdir()

    assert_denied(candidate, (root,))


def test_rejects_missing_target_without_creating_it(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    missing = root / "missing.txt"

    assert_denied("missing.txt", (root,))

    assert not missing.exists()


def test_rejects_case_prefix_tricks_without_string_prefix_matching(tmp_path: Path) -> None:
    root = tmp_path / "Root"
    root.mkdir()
    sibling = write_file(tmp_path / "Root-private" / "secret.txt")

    assert_denied(str(sibling), (root,))


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = write_file(tmp_path / "outside.txt")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314 or error.errno in {
            errno.EPERM,
            errno.EACCES,
        }:
            pytest.skip(f"cannot create symlink in this environment: {error}")
        raise

    assert_denied("linked.txt", (root,))


def test_rejects_windows_junction_escape(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions are only available on Windows")
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    write_file(outside / "secret.txt")
    junction = root / "jump"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot create Windows junction in this environment: {result.stderr}")

    assert_denied(r"jump\secret.txt", (root,))

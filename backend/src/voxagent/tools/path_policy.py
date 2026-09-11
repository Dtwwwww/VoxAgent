from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath


class PathAuthorizationError(ValueError):
    """Raised when a tool path is outside the authorized filesystem boundary."""


class PathPolicy:
    @staticmethod
    def resolve_authorized(candidate: str, roots: Sequence[Path]) -> Path:
        resolved_roots = tuple(_resolve_root(root) for root in roots)
        if not resolved_roots:
            raise _authorization_error()
        if _is_unc_or_device_path(candidate):
            raise _authorization_error()

        candidate_path = Path(candidate).expanduser()
        candidate_paths = (
            (candidate_path,)
            if candidate_path.is_absolute()
            else tuple(root / candidate_path for root in resolved_roots)
        )
        for path in candidate_paths:
            resolved = _resolve_candidate(path)
            if resolved is None:
                continue
            for root in resolved_roots:
                if resolved.is_relative_to(root) and _components_remain_inside(path, root):
                    return resolved
        raise _authorization_error()


def _authorization_error() -> PathAuthorizationError:
    return PathAuthorizationError("path is not authorized")


def _resolve_root(root: Path) -> Path:
    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError as error:
        raise _authorization_error() from error
    if not resolved.is_dir():
        raise _authorization_error()
    return resolved


def _resolve_candidate(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except OSError:
        return None


def _is_unc_or_device_path(candidate: str) -> bool:
    text = candidate.strip()
    if text.startswith(("\\\\", "//")):
        return True
    drive = PureWindowsPath(text).drive
    return drive.startswith("\\\\")


def _components_remain_inside(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True

    current = root
    for part in relative.parts:
        current = current / part
        if _is_link_or_reparse_point(current):
            try:
                if not current.resolve(strict=True).is_relative_to(root):
                    return False
            except OSError:
                return False
    return True


def _is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

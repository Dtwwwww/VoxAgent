import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "download_speech_models.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _make_archive(path: Path, directory_name: str, required_files: tuple[str, ...]) -> None:
    source_root = path.parent / "archive-source"
    model_dir = source_root / directory_name
    model_dir.mkdir(parents=True)
    for relative in required_files:
        target = model_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"fixture:{relative}", encoding="utf-8")
    with tarfile.open(path, "w:bz2") as archive:
        archive.add(model_dir, arcname=directory_name)


def _write_manifest(
    path: Path,
    *,
    source: Path,
    archive_sha256: str,
    required_files: tuple[str, ...],
) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "Name": "fixture-model",
                    "Archive": "fixture.tar.bz2",
                    "Directory": "fixture-model-v1",
                    "Url": str(source),
                    "Version": "test-v1",
                    "ArchiveSha256": archive_sha256,
                    "RequiredFiles": list(required_files),
                }
            ]
        ),
        encoding="utf-8",
    )


def _run_script(data_root: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-DataRoot",
            str(data_root),
            "-ModelManifestPath",
            str(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_bad_cached_archive_is_replaced_then_valid_install_is_idempotent(tmp_path):
    required_files = ("model.onnx", "tokens.txt")
    good_source = tmp_path / "good-source.tar.bz2"
    _make_archive(good_source, "fixture-model-v1", required_files)
    expected_hash = hashlib.sha256(good_source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=good_source,
        archive_sha256=expected_hash,
        required_files=required_files,
    )
    data_root = tmp_path / "data"
    model_root = data_root / "models" / "speech"
    model_root.mkdir(parents=True)
    cached_archive = model_root / "fixture.tar.bz2"
    cached_archive.write_bytes(b"corrupt cached archive")
    (model_root / "fixture.tar.bz2.part").write_bytes(b"stale partial download")

    first = _run_script(data_root, manifest)

    assert first.returncode == 0, first.stderr
    assert hashlib.sha256(cached_archive.read_bytes()).hexdigest() == expected_hash
    assert not (model_root / "fixture.tar.bz2.part").exists()
    installed = model_root / "fixture-model-v1"
    assert all((installed / relative).is_file() for relative in required_files)
    marker = json.loads((installed / ".voxagent-complete").read_text(encoding="utf-8-sig"))
    assert marker["version"] == "test-v1"
    assert marker["archive_sha256"] == expected_hash

    good_source.unlink()
    second = _run_script(data_root, manifest)

    assert second.returncode == 0, second.stderr
    assert "Present: fixture-model" in second.stdout


def test_failed_extraction_preserves_existing_target(tmp_path):
    bad_source = tmp_path / "bad-source.tar.bz2"
    bad_source.write_bytes(b"not a bzip2 tar archive")
    expected_hash = hashlib.sha256(bad_source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=bad_source,
        archive_sha256=expected_hash,
        required_files=("model.onnx",),
    )
    data_root = tmp_path / "data"
    existing = data_root / "models" / "speech" / "fixture-model-v1"
    existing.mkdir(parents=True)
    sentinel = existing / "keep-me.txt"
    sentinel.write_text("original", encoding="utf-8")

    result = _run_script(data_root, manifest)

    assert result.returncode != 0
    assert "Extraction failed for fixture-model" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "original"
    assert not (existing / ".voxagent-complete").exists()
    assert not (data_root / "models" / "speech" / "fixture.tar.bz2").exists()


def test_archive_missing_required_files_is_not_published(tmp_path):
    source = tmp_path / "missing-model.tar.bz2"
    _make_archive(source, "fixture-model-v1", ("tokens.txt",))
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=source,
        archive_sha256=expected_hash,
        required_files=("model.onnx", "tokens.txt"),
    )
    data_root = tmp_path / "data"

    result = _run_script(data_root, manifest)

    assert result.returncode != 0
    assert "one or more required files are missing" in result.stderr
    assert not (data_root / "models" / "speech" / "fixture-model-v1").exists()

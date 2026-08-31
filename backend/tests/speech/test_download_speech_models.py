import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "download_speech_models.ps1"
SHARED_MANIFEST = REPO_ROOT / "backend" / "src" / "voxagent" / "speech" / "models.json"
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
    directory: str = "fixture-model-v1",
) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "Name": "fixture-model",
                    "Archive": "fixture.tar.bz2",
                    "Directory": directory,
                    "Url": str(source),
                    "Version": "test-v1",
                    "ArchiveSha256": archive_sha256,
                    "RequiredFiles": list(required_files),
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_file_manifest(
    path: Path,
    *,
    source: Path,
    archive_sha256: str,
    directory: str = "silero-vad",
    filename: str = "silero_vad.onnx",
) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "Name": "silero-vad",
                    "Format": "file",
                    "Archive": filename,
                    "Directory": directory,
                    "Url": source.as_uri(),
                    "Version": "test-v1",
                    "ArchiveSha256": archive_sha256,
                    "RequiredFiles": [filename],
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_selection_manifest(
    path: Path,
    *,
    invalid_archive_source: Path,
    file_source: Path,
) -> None:
    invalid_archive_sha256 = hashlib.sha256(invalid_archive_source.read_bytes()).hexdigest()
    file_sha256 = hashlib.sha256(file_source.read_bytes()).hexdigest()
    path.write_text(
        json.dumps(
            [
                {
                    "Name": "legacy-invalid",
                    "Archive": "legacy-invalid.tar.bz2",
                    "Directory": "legacy-invalid",
                    "Url": str(invalid_archive_source),
                    "Version": "test-v1",
                    "ArchiveSha256": invalid_archive_sha256,
                    "RequiredFiles": ["model.onnx"],
                },
                {
                    "Name": "silero-vad",
                    "Format": "file",
                    "Archive": "silero_vad.onnx",
                    "Directory": "silero-vad",
                    "Url": file_source.as_uri(),
                    "Version": "test-v1",
                    "ArchiveSha256": file_sha256,
                    "RequiredFiles": ["silero_vad.onnx"],
                },
            ]
        ),
        encoding="utf-8",
    )


def _run_script(
    data_root: Path,
    manifest: Path,
    *,
    authorize_custom_manifest: bool = True,
    model_names: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS"] = "1"
    command = [
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
        "-SkipPreflightForTests",
    ]
    if authorize_custom_manifest:
        environment["VOXAGENT_ALLOW_TEST_MODEL_MANIFEST"] = "1"
        command.append("-AllowCustomManifestForTests")
    if model_names:
        command.extend(("-ModelName", *model_names))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
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


def test_file_asset_install_is_idempotent_and_stays_within_model_root(tmp_path):
    source = tmp_path / "silero_vad.onnx"
    source.write_bytes(b"valid local file model")
    manifest = tmp_path / "manifest.json"
    _write_file_manifest(
        manifest,
        source=source,
        archive_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    data_root = tmp_path / "data"

    first = _run_script(data_root, manifest)

    assert first.returncode == 0, first.stderr
    installed = data_root / "models" / "speech" / "silero-vad" / "silero_vad.onnx"
    assert installed.read_bytes() == source.read_bytes()
    assert not (tmp_path / "silero-vad" / "silero_vad.onnx").exists()

    source.unlink()
    second = _run_script(data_root, manifest)

    assert second.returncode == 0, second.stderr
    assert "Present: silero-vad" in second.stdout


def test_file_asset_checksum_rejection_never_publishes_target(tmp_path):
    source = tmp_path / "silero_vad.onnx"
    source.write_bytes(b"wrong model bytes")
    manifest = tmp_path / "manifest.json"
    _write_file_manifest(manifest, source=source, archive_sha256="0" * 64)

    result = _run_script(tmp_path / "data", manifest)

    assert result.returncode != 0
    assert "Checksum mismatch for silero-vad" in result.stderr
    assert not (tmp_path / "data" / "models" / "speech" / "silero-vad").exists()


def test_file_asset_replaces_corrupt_completed_target(tmp_path):
    source = tmp_path / "silero_vad.onnx"
    source.write_bytes(b"valid local file model")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    _write_file_manifest(manifest, source=source, archive_sha256=expected_hash)
    data_root = tmp_path / "data"
    installed = data_root / "models" / "speech" / "silero-vad"
    installed.mkdir(parents=True)
    (installed / "silero_vad.onnx").write_bytes(b"corrupt completed model")
    (installed / ".voxagent-complete").write_text(
        json.dumps({"version": "test-v1", "archive_sha256": expected_hash}),
        encoding="utf-8",
    )

    result = _run_script(data_root, manifest)

    assert result.returncode == 0, result.stderr
    assert (installed / "silero_vad.onnx").read_bytes() == source.read_bytes()


def test_model_name_selects_only_requested_file_asset(tmp_path):
    invalid_archive = tmp_path / "legacy-invalid.tar.bz2"
    invalid_archive.write_bytes(b"not a tar archive")
    file_source = tmp_path / "silero_vad.onnx"
    file_source.write_bytes(b"valid selected file model")
    manifest = tmp_path / "manifest.json"
    _write_selection_manifest(
        manifest,
        invalid_archive_source=invalid_archive,
        file_source=file_source,
    )
    data_root = tmp_path / "data"

    result = _run_script(data_root, manifest, model_names=("silero-vad",))

    assert result.returncode == 0, result.stderr
    model_root = data_root / "models" / "speech"
    assert (model_root / "silero-vad" / "silero_vad.onnx").read_bytes() == file_source.read_bytes()
    assert not (model_root / "legacy-invalid").exists()
    assert not (model_root / "legacy-invalid.tar.bz2").exists()


def test_unknown_model_name_fails_before_any_model_root_write(tmp_path):
    invalid_archive = tmp_path / "legacy-invalid.tar.bz2"
    invalid_archive.write_bytes(b"not a tar archive")
    file_source = tmp_path / "silero_vad.onnx"
    file_source.write_bytes(b"valid selected file model")
    manifest = tmp_path / "manifest.json"
    _write_selection_manifest(
        manifest,
        invalid_archive_source=invalid_archive,
        file_source=file_source,
    )
    data_root = tmp_path / "data"

    result = _run_script(data_root, manifest, model_names=("not-in-manifest",))

    assert result.returncode != 0
    assert "Unknown model name: not-in-manifest" in result.stderr
    assert not (data_root / "models" / "speech").exists()


def test_custom_manifest_requires_explicit_double_test_gate(tmp_path):
    source = tmp_path / "model.tar.bz2"
    _make_archive(source, "fixture-model-v1", ("model.onnx",))
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=source,
        archive_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        required_files=("model.onnx",),
    )

    result = _run_script(
        tmp_path / "data",
        manifest,
        authorize_custom_manifest=False,
    )

    assert result.returncode != 0
    assert "Custom model manifests are disabled" in result.stderr


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("Name", "..\\outside"),
        ("Archive", "..\\outside.tar.bz2"),
        ("Directory", "..\\outside"),
        ("RequiredFiles", ["..\\sentinel.txt"]),
    ],
)
def test_manifest_paths_are_rejected_without_touching_sentinel(
    tmp_path,
    field,
    unsafe_value,
):
    source = tmp_path / "model.tar.bz2"
    _make_archive(source, "fixture-model-v1", ("model.onnx",))
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=source,
        archive_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        required_files=("model.onnx",),
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[0][field] = unsafe_value
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = _run_script(tmp_path / "data", manifest)

    assert result.returncode != 0
    assert "Unsafe manifest path" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_traversal_archive_is_rejected_before_extraction(tmp_path):
    source = tmp_path / "traversal.tar.bz2"
    with tarfile.open(source, "w:bz2") as archive:
        model = tarfile.TarInfo("fixture-model-v1/model.onnx")
        model.size = 5
        archive.addfile(model, io.BytesIO(b"model"))
        traversal = tarfile.TarInfo("../../escape.txt")
        traversal.size = 6
        archive.addfile(traversal, io.BytesIO(b"escape"))
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=source,
        archive_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        required_files=("model.onnx",),
    )
    sentinel = tmp_path / "escape.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = _run_script(tmp_path / "data", manifest)

    assert result.returncode != 0
    assert "Unsafe archive member" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_link_archive_member_is_rejected_before_extraction(tmp_path, member_type):
    source = tmp_path / "link.tar.bz2"
    with tarfile.open(source, "w:bz2") as archive:
        model = tarfile.TarInfo("fixture-model-v1/model.onnx")
        model.size = 5
        archive.addfile(model, io.BytesIO(b"model"))
        link = tarfile.TarInfo("fixture-model-v1/link")
        link.type = member_type
        link.linkname = "../../sentinel.txt"
        archive.addfile(link)
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        source=source,
        archive_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        required_files=("model.onnx",),
    )
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = _run_script(tmp_path / "data", manifest)

    assert result.returncode != 0
    assert "Unsafe archive member type" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_shared_manifest_is_the_only_speech_model_inventory():
    payload = json.loads(SHARED_MANIFEST.read_text(encoding="utf-8"))
    script = SCRIPT.read_text(encoding="utf-8-sig")

    assert len(payload) == 4
    assert all(len(model["ArchiveSha256"]) == 64 for model in payload)
    assert payload[-1] == {
        "Name": "silero-vad",
        "Format": "file",
        "Archive": "silero_vad.onnx",
        "Url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "silero_vad.onnx"
        ),
        "Directory": "silero-vad",
        "Version": "silero_vad.onnx",
        "Source": "k2-fsa/sherpa-onnx release asr-models (MIT)",
        "ArchiveSha256": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        "RequiredFiles": ["silero_vad.onnx"],
    }
    assert "models.json" in script
    assert "sensevoice-int8" not in script

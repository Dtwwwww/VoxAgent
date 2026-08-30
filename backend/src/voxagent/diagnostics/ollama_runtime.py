from __future__ import annotations

import hashlib
import ipaddress
import json
import ntpath
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath

import httpx
import psutil

from voxagent.diagnostics.baseline_validator import validate_baseline


@dataclass(frozen=True, slots=True)
class OllamaRuntimeExpectation:
    version: str
    model_digests: dict[str, str]
    local_manifest_relative_paths: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OllamaRuntimeObservation:
    listener_addresses: tuple[str, ...]
    api_version: str | None
    api_model_digests: dict[str, str]
    local_manifest_digests: dict[str, str | None]
    server_environment: dict[str, str] | None
    selected_models_root: str
    collection_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OllamaRuntimeIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class OllamaRuntimeReport:
    valid: bool
    offline_status: str
    issues: tuple[OllamaRuntimeIssue, ...]
    observation: OllamaRuntimeObservation

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "offline_status": self.offline_status,
            "issues": [asdict(issue) for issue in self.issues],
            "observation": asdict(self.observation),
        }


def _issue(code: str, message: str) -> OllamaRuntimeIssue:
    return OllamaRuntimeIssue(code=code, message=message)


def _is_loopback_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _normalize_windows_path(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def evaluate_ollama_runtime(
    expectation: OllamaRuntimeExpectation,
    observation: OllamaRuntimeObservation,
) -> OllamaRuntimeReport:
    issues = [
        _issue("ollama_observation_error", error) for error in observation.collection_errors
    ]
    if not observation.listener_addresses:
        issues.append(
            _issue("ollama_listener_missing", "No Ollama listener was found on port 11434.")
        )
    elif any(not _is_loopback_address(address) for address in observation.listener_addresses):
        issues.append(
            _issue(
                "ollama_listener_not_loopback",
                "Every Ollama listener must be bound only to a loopback address.",
            )
        )

    if observation.api_version != expectation.version:
        issues.append(
            _issue(
                "ollama_version_mismatch",
                f"Expected Ollama {expectation.version}, observed {observation.api_version!r}.",
            )
        )

    if set(observation.api_model_digests) != set(expectation.model_digests):
        issues.append(
            _issue(
                "ollama_api_inventory_mismatch",
                "Ollama API tags must exactly equal the committed baseline model set.",
            )
        )

    for model, expected_digest in expectation.model_digests.items():
        if observation.api_model_digests.get(model) != expected_digest:
            issues.append(
                _issue(
                    "ollama_api_digest_mismatch",
                    f"API digest for {model} does not match the committed baseline.",
                )
            )
        if observation.local_manifest_digests.get(model) != expected_digest:
            issues.append(
                _issue(
                    "ollama_local_manifest_mismatch",
                    f"Selected-root manifest for {model} does not match the committed baseline.",
                )
            )

    offline_verified = bool(
        observation.server_environment
        and observation.server_environment.get("OLLAMA_NO_CLOUD") == "1"
    )
    if not offline_verified:
        issues.append(
            _issue(
                "ollama_offline_unverified",
                "The existing Ollama server's OLLAMA_NO_CLOUD=1 environment could not be verified.",
            )
        )
    observed_models_root = (
        observation.server_environment.get("OLLAMA_MODELS")
        if observation.server_environment
        else None
    )
    if not observed_models_root:
        issues.append(
            _issue(
                "ollama_models_root_unverified",
                "The existing Ollama server's OLLAMA_MODELS environment could not be verified.",
            )
        )
    elif _normalize_windows_path(observed_models_root) != _normalize_windows_path(
        observation.selected_models_root
    ):
        issues.append(
            _issue(
                "ollama_models_root_mismatch",
                "The existing Ollama server is not using the selected-root models directory.",
            )
        )
    return OllamaRuntimeReport(
        valid=not issues,
        offline_status="verified" if offline_verified else "unverified",
        issues=tuple(issues),
        observation=observation,
    )


def load_ollama_runtime_expectation(baseline_path: Path) -> OllamaRuntimeExpectation:
    baseline_issues = validate_baseline(baseline_path)
    if baseline_issues:
        raise ValueError("Committed baseline is invalid: " + "; ".join(baseline_issues))
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    model_digests: dict[str, str] = {}
    manifest_paths: dict[str, str] = {}
    prefix = "${DATA_ROOT}\\models\\ollama\\"
    for candidate in payload["llm_candidates"]:
        model = candidate["model_id"]
        identity = candidate["model_identity"]
        model_digests[model] = identity["ollama_tag_digest"]
        declared_path = identity["local_manifest_path"]
        if not declared_path.startswith(prefix):
            raise ValueError(f"Unexpected local manifest template for {model}")
        manifest_paths[model] = declared_path.removeprefix(prefix)
    return OllamaRuntimeExpectation(
        version=payload["collection"]["ollama_version"],
        model_digests=model_digests,
        local_manifest_relative_paths=manifest_paths,
    )


def _selected_root_manifest_digests(
    data_root: Path,
    expectation: OllamaRuntimeExpectation,
) -> dict[str, str | None]:
    manifest_root = (data_root / "models" / "ollama").resolve()
    result: dict[str, str | None] = {}
    for model, relative in expectation.local_manifest_relative_paths.items():
        relative_parts = PureWindowsPath(relative).parts
        candidate = (manifest_root.joinpath(*relative_parts)).resolve()
        if not candidate.is_relative_to(manifest_root) or not candidate.is_file():
            result[model] = None
            continue
        result[model] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return result


def collect_ollama_runtime_observation(
    data_root: Path,
    expectation: OllamaRuntimeExpectation,
    *,
    endpoint: str = "http://127.0.0.1:11434",
) -> OllamaRuntimeObservation:
    errors: list[str] = []
    listener_addresses: list[str] = []
    listener_pids: set[int] = set()
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                continue
            address, port = connection.laddr[0], connection.laddr[1]
            if port == 11434:
                listener_addresses.append(address)
                if connection.pid is not None:
                    listener_pids.add(connection.pid)
    except (OSError, psutil.AccessDenied) as error:
        errors.append(f"listener inspection failed: {error}")

    api_version: str | None = None
    api_models: dict[str, str] = {}
    try:
        with httpx.Client(base_url=endpoint, trust_env=False, timeout=5) as client:
            version_response = client.get("/api/version")
            version_response.raise_for_status()
            api_version = str(version_response.json().get("version") or "") or None
            tags_response = client.get("/api/tags")
            tags_response.raise_for_status()
            for item in tags_response.json().get("models", []):
                digest = str(item.get("digest") or "")
                name = item.get("name") or item.get("model")
                if name and digest:
                    api_models[str(name)] = digest
    except (httpx.HTTPError, ValueError, TypeError) as error:
        errors.append(f"Ollama API inspection failed: {error}")

    server_environment: dict[str, str] | None = None
    if len(listener_pids) == 1:
        try:
            process_environment = psutil.Process(next(iter(listener_pids))).environ()
            normalized_environment = {
                key.upper(): value for key, value in process_environment.items()
            }
            server_environment = {
                key: normalized_environment[key]
                for key in ("OLLAMA_NO_CLOUD", "OLLAMA_HOST", "OLLAMA_MODELS")
                if key in normalized_environment
            }
        except (OSError, psutil.Error):
            server_environment = None

    return OllamaRuntimeObservation(
        listener_addresses=tuple(sorted(set(listener_addresses))),
        api_version=api_version,
        api_model_digests=api_models,
        local_manifest_digests=_selected_root_manifest_digests(data_root, expectation),
        server_environment=server_environment,
        selected_models_root=str((data_root / "models" / "ollama").resolve()),
        collection_errors=tuple(errors),
    )


def verify_ollama_runtime(
    *,
    data_root: Path,
    baseline_path: Path,
) -> OllamaRuntimeReport:
    expectation = load_ollama_runtime_expectation(baseline_path)
    observation = collect_ollama_runtime_observation(data_root, expectation)
    return evaluate_ollama_runtime(expectation, observation)

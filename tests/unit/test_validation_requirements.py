"""Dependency readiness is explicit, confined and bound to the request proof."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from rlm.validation.environment import scope_identity
from rlm.validation.models import ValidationRequest, ValidationRequirements
from rlm.validation.profiles import probe_script, runtime_commands
from rlm.validation.runner import canonical, request_hash


def request(tmp_path, **kwargs):
    return ValidationRequest(
        workspace=str(tmp_path), argv=["python", "-V"], image="prepared", **kwargs
    )


@pytest.mark.parametrize(
    "requirements",
    [
        {"python_modules": ["os;touch /tmp/injected"]},
        {"python_modules": ["../secrets"]},
        {"node_modules": ["../../private"]},
        {"node_modules": ["package/submodule"]},
        {"commands": ["sh -c id"]},
        {"commands": ["/bin/sh"]},
        {"commands": ["$(id)"]},
        {"commands": [123]},
        {"commands": ["a" * 129]},
        {"python_modules": ["json"] * 33},
        {"install": ["fastapi"]},
    ],
)
def test_requirements_refuse_injection_paths_types_and_unbounded_input(tmp_path, requirements):
    with pytest.raises(ValidationError):
        request(tmp_path, requirements=requirements)


def test_scoped_node_packages_and_dotted_python_modules_are_supported():
    requirements = ValidationRequirements(
        node_modules=["@scope/pkg", "typescript"],
        python_modules=["json.tool", "fastapi", "fastapi"],
        commands=["pdftotext", "g++"],
    )
    assert requirements.python_modules == ["json.tool", "fastapi"]


def test_empty_requirements_preserve_legacy_v1_receipt_and_choice_identity(tmp_path):
    req = request(tmp_path)
    legacy = req.model_dump(exclude={"requirements"})
    assert request_hash(req) == hashlib.sha256(canonical(legacy)).hexdigest()
    parts = [
        "snipara.validation.scope.v2",
        "lot",
        req.schema_version,
        str(tmp_path.resolve()),
        req.profile,
        req.image,
        req.cwd,
        str(req.timeout_seconds),
        str(int(req.cpus * 1_000_000_000)),
        str(req.memory_mb),
        str(req.pids),
        str(req.disk_mb),
    ]
    assert scope_identity(req, "lot") == hashlib.sha256(canonical(parts)).hexdigest()


def test_nonempty_requirements_use_independently_reproducible_scope_v3(tmp_path):
    req = request(tmp_path, requirements={"python_modules": ["fastapi"]})
    parts = [
        "snipara.validation.scope.v3",
        "lot",
        req.schema_version,
        str(tmp_path.resolve()),
        req.profile,
        req.image,
        req.cwd,
        str(req.timeout_seconds),
        str(int(req.cpus * 1_000_000_000)),
        str(req.memory_mb),
        str(req.pids),
        str(req.disk_mb),
        json.dumps(["fastapi"], separators=(",", ":")),
        "[]",
        "[]",
    ]
    assert scope_identity(req, "lot") == hashlib.sha256(canonical(parts)).hexdigest()


def test_published_scope_v3_vector_matches_independent_consumer():
    contract = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/validation-scope-v3.json").read_text()
    )
    for case in contract["cases"]:
        req = ValidationRequest.model_validate(case["request"])
        expected = hashlib.sha256(case["preimage"].encode("utf-8")).hexdigest()
        assert scope_identity(req, case["scope"]) == expected == case["sha256"]


def test_capability_changes_invalidate_request_and_saved_choice(tmp_path):
    baseline = request(tmp_path)
    changed = request(tmp_path, requirements={"python_modules": ["fastapi"]})
    assert request_hash(baseline) != request_hash(changed)
    assert scope_identity(baseline, "lot") != scope_identity(changed, "lot")


def test_probe_imports_modules_and_rejects_missing_dependency_before_execution(tmp_path):
    req = request(tmp_path, requirements={"python_modules": ["json"]})
    ready = subprocess.run(["sh", "-c", probe_script(req)], capture_output=True, text=True)
    assert ready.returncode == 0 and "python module ready: json" in ready.stdout
    missing = request(
        tmp_path, requirements={"python_modules": ["snipara_nonexistent_control_20261024"]}
    )
    failed = subprocess.run(["sh", "-c", probe_script(missing)], capture_output=True, text=True)
    assert failed.returncode != 0
    assert "snipara_nonexistent_control_20261024" in failed.stderr


def test_missing_executable_is_reported_and_stops_later_probes(tmp_path):
    req = request(tmp_path, requirements={"commands": ["snipara_missing_binary_control"]})
    result = subprocess.run(["sh", "-c", probe_script(req)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "missing command: snipara_missing_binary_control" in result.stdout


def test_cross_runtime_requirements_request_the_needed_interpreter(tmp_path):
    req = request(tmp_path, requirements={"node_modules": ["typescript"]})
    assert "node" in runtime_commands(req)


def test_mcp_schema_exposes_strict_requirements():
    schema = ValidationRequest.model_json_schema()
    assert "requirements" in schema["properties"]
    requirements = schema["$defs"]["ValidationRequirements"]
    assert requirements["additionalProperties"] is False
    assert requirements["properties"]["python_modules"]["maxItems"] == 32


def test_diagnose_preserves_requirements_and_pnpm_inference(tmp_path, monkeypatch):
    from rlm.validation import runner

    req = ValidationRequest(
        workspace=str(tmp_path),
        argv=["pnpm", "test"],
        image="prepared",
        profile="mixed",
        requirements={"python_modules": ["fastapi"]},
    )
    client = Mock()
    client.images.get.return_value.id = "sha256:prepared"
    client.version.return_value = {"Version": "test"}
    monkeypatch.setattr(runner, "capture_bounded", lambda *a, **k: {})

    def failed_probe(probe, *args, **kwargs):
        assert "pnpm" in probe.requirements.commands
        assert probe.requirements.python_modules == ["fastapi"]
        assert probe.argv == ["true"]
        return {"status": "environment_incompatible", "runtime_versions": "missing: fastapi"}

    monkeypatch.setattr(runner, "run", failed_probe)
    report = runner.diagnose(req, client=client)
    assert report["ready"] is False
    assert report["status"] == "incompatible"
    assert report["runtime_versions"] == "missing: fastapi"

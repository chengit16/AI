import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.check_release_readiness import evidence_status
from scripts.create_release_bundle import create_bundle
from scripts.generate_supply_chain import stale_outputs

ROOT = Path(__file__).parents[1]
SUPPLY_CHAIN_DIR = ROOT / "docs" / "supply-chain"
SBOM_PATHS = (
    SUPPLY_CHAIN_DIR / "python-production.cdx.json",
    SUPPLY_CHAIN_DIR / "node-production.cdx.json",
)
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_production_sboms_use_cyclonedx_15() -> None:
    for path in SBOM_PATHS:
        document = read_json(path)

        assert document["bomFormat"] == "CycloneDX"
        assert document["specVersion"] == "1.5"
        assert document["version"] == 1
        assert document["components"]


def test_supply_chain_outputs_do_not_contain_machine_specific_metadata() -> None:
    for path in (*SBOM_PATHS, SUPPLY_CHAIN_DIR / "dependency-licenses.json"):
        content = path.read_text(encoding="utf-8")
        document = read_json(path)

        assert "timestamp" not in document.get("metadata", {})
        assert "serialNumber" not in document
        assert str(ROOT) not in content
        assert "/Users/" not in content
        assert UUID_PATTERN.search(content) is None


def test_installed_python_production_licenses_are_known() -> None:
    inventory = read_json(SUPPLY_CHAIN_DIR / "dependency-licenses.json")

    assert inventory["summary"]["unknown_python_licenses"] == []
    assert inventory["summary"]["python_packages"] == len(inventory["python"])
    assert inventory["summary"]["node_packages"] == len(inventory["node"])


def test_stale_outputs_detects_missing_and_changed_files(tmp_path: Path) -> None:
    current = tmp_path / "current.json"
    changed = tmp_path / "changed.json"
    missing = tmp_path / "missing.json"
    current.write_text("current\n", encoding="utf-8")
    changed.write_text("old\n", encoding="utf-8")

    assert stale_outputs(
        {
            current: "current\n",
            changed: "new\n",
            missing: "new\n",
        }
    ) == [changed, missing]


def test_missing_external_evidence_keeps_explicit_status(tmp_path: Path) -> None:
    status, detail = evidence_status(
        tmp_path / "missing.json",
        missing="not_configured",
        kind="image_vulnerability_scan",
    )

    assert status == "not_configured"
    assert detail.startswith("未提供已验证证据:")


def test_image_evidence_rejects_unverified_passed_marker(tmp_path: Path) -> None:
    evidence = tmp_path / "image-scan.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "image_vulnerability_scan",
                "status": "passed",
            }
        ),
        encoding="utf-8",
    )

    status, detail = evidence_status(
        evidence,
        missing="not_configured",
        kind="image_vulnerability_scan",
    )

    assert status == "failed"
    assert detail.startswith("镜像扫描证据字段无效:")


def test_release_bundle_rejects_blocked_gate(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps({"schema_version": 1, "release_status": "blocked"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="正式发布门禁尚未通过"):
        create_bundle(
            manifest=tmp_path / "manifest.json",
            readiness=readiness,
            output=tmp_path / "bundle",
            files=(),
        )


def test_release_bundle_writes_stable_checksums(tmp_path: Path) -> None:
    manifest = tmp_path / "release-manifest.json"
    readiness = tmp_path / "readiness.json"
    component = tmp_path / "component.json"
    manifest.write_text('{"release":"synthetic"}\n', encoding="utf-8")
    readiness.write_text(
        json.dumps({"schema_version": 1, "release_status": "passed"}), encoding="utf-8"
    )
    component.write_text('{"component":"synthetic"}\n', encoding="utf-8")
    output = tmp_path / "bundle"

    create_bundle(
        manifest=manifest,
        readiness=readiness,
        output=output,
        files=(component,),
    )

    checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == 3
    assert checksum_lines == sorted(
        checksum_lines, key=lambda line: line.split("  ", maxsplit=1)[1]
    )

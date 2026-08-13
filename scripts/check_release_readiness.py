"""汇总开发与正式发布所需的供应链门禁状态。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal, TypedDict

from scripts.check_repository_policy import collect_history_violations, collect_violations
from scripts.generate_contract_types import stale_outputs as stale_contract_outputs
from scripts.generate_supply_chain import expected_outputs
from scripts.generate_supply_chain import stale_outputs as stale_supply_outputs

ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "supply-chain" / "local-readiness.v1.json"
IMAGE_EVIDENCE = ROOT / "artifacts" / "security" / "image-scan.v1.json"
LINUX_EVIDENCE = ROOT / "artifacts" / "security" / "linux-validation.v1.json"
Status = Literal["passed", "failed", "not_configured", "not_run"]


class Check(TypedDict):
    status: Status
    required_for_development: bool
    required_for_release: bool
    detail: str


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def local_check(status: Status, detail: str) -> Check:
    return {
        "status": status,
        "required_for_development": True,
        "required_for_release": True,
        "detail": detail,
    }


def external_check(status: Status, detail: str) -> Check:
    return {
        "status": status,
        "required_for_development": False,
        "required_for_release": True,
        "detail": detail,
    }


def evidence_status(path: Path, *, missing: Status, kind: str) -> tuple[Status, str]:
    if not path.is_file():
        return missing, f"未提供已验证证据: {display_path(path)}"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "failed", f"证据不是有效 JSON: {display_path(path)}"
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("kind") != kind
    ):
        return "failed", f"证据 Schema 无效: {display_path(path)}"
    status = document.get("status")
    if status != "passed":
        return "failed", f"证据没有通过: {display_path(path)}"
    if kind == "image_vulnerability_scan":
        scanner = document.get("scanner")
        images = document.get("images")
        valid_scanners = {"docker_scout", "grype", "trivy"}
        valid_images = (
            isinstance(images, list)
            and bool(images)
            and all(
                isinstance(image, dict)
                and isinstance(image.get("digest"), str)
                and image["digest"].startswith("sha256:")
                and len(image["digest"]) == 71
                and image.get("critical_vulnerabilities") == 0
                and image.get("high_vulnerabilities") == 0
                for image in images
            )
        )
        if scanner not in valid_scanners or not document.get("scanner_version") or not valid_images:
            return "failed", f"镜像扫描证据字段无效: {display_path(path)}"
    if kind == "linux_host_validation":
        required_checks = {"verify", "platform_doctor"}
        checks = document.get("checks")
        if (
            document.get("os") != "linux"
            or not isinstance(checks, list)
            or not required_checks.issubset(checks)
        ):
            return "failed", f"Linux 验收证据字段无效: {display_path(path)}"
    return "passed", f"已验证证据: {display_path(path)}"


def build_report() -> dict[str, object]:
    contract_stale = stale_contract_outputs()
    current_secrets = collect_violations()
    history_secrets = collect_history_violations()
    supply_stale = stale_supply_outputs(expected_outputs())
    image_status, image_detail = evidence_status(
        IMAGE_EVIDENCE,
        missing="not_configured",
        kind="image_vulnerability_scan",
    )
    linux_status, linux_detail = evidence_status(
        LINUX_EVIDENCE,
        missing="not_run",
        kind="linux_host_validation",
    )
    checks: dict[str, Check] = {
        "contract_types": local_check(
            "failed" if contract_stale else "passed",
            "React/Python 生成类型存在漂移" if contract_stale else "跨语言契约生成产物可复现",
        ),
        "repository_current_secrets": local_check(
            "failed" if current_secrets else "passed",
            "当前文件发现高风险凭证" if current_secrets else "当前文件未发现高风险凭证",
        ),
        "repository_history_secrets": local_check(
            "failed" if history_secrets else "passed",
            "Git 历史发现高风险凭证" if history_secrets else "完整 Git 历史未发现高风险凭证",
        ),
        "supply_chain_inventory": local_check(
            "failed" if supply_stale else "passed",
            "SBOM 或许可证清单存在漂移" if supply_stale else "SBOM 与许可证清单可复现",
        ),
        "release_manifest": local_check(
            "passed",
            "ReleaseManifest Schema、兼容矩阵与生成漂移由统一门禁校验",
        ),
        "image_vulnerability_scan": external_check(image_status, image_detail),
        "linux_host_validation": external_check(linux_status, linux_detail),
    }
    development_passed = all(
        check["status"] == "passed"
        for check in checks.values()
        if check["required_for_development"]
    )
    release_passed = all(
        check["status"] == "passed" for check in checks.values() if check["required_for_release"]
    )
    return {
        "schema_version": 1,
        "generator_versions": {
            "openapi_typescript": "7.13.0",
            "python_contract_generator": "p1a-06-v1",
            "repository_secret_scanner": "p1a-06-v1",
            "release_bundle": "p1a-06-v1",
        },
        "development_status": "passed" if development_passed else "blocked",
        "release_status": "passed" if release_passed else "blocked",
        "checks": checks,
    }


def stable_json(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", choices=("development", "release"), default="development")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="检查已提交状态清单是否漂移")
    arguments = parser.parse_args()
    report = build_report()
    content = stable_json(report)
    if arguments.check:
        if (
            not arguments.output.is_file()
            or arguments.output.read_text(encoding="utf-8") != content
        ):
            print(f"供应链门禁状态需要重新生成: {arguments.output}")
            return 1
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(content, encoding="utf-8")
        print(f"已生成供应链门禁状态: {arguments.output}")
    status_key = f"{arguments.require}_status"
    status = report[status_key]
    print(f"{arguments.require} 门禁状态: {status}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""从冻结锁文件和本地包元数据生成可复现的 SBOM 与许可证清单。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import defaultdict
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).parents[1]
OUTPUT_DIR = ROOT / "docs" / "supply-chain"
PYTHON_SBOM = OUTPUT_DIR / "python-production.cdx.json"
NODE_SBOM = OUTPUT_DIR / "node-production.cdx.json"
LICENSE_INVENTORY = OUTPUT_DIR / "dependency-licenses.json"
PYTHON_DIRECT = {
    "alembic",
    "fastapi",
    "pdfplumber",
    "pgvector",
    "psycopg",
    "pydantic-settings",
    "sqlalchemy",
    "uvicorn",
}
NODE_IMPORTER = "apps/web"


def run_json(command: list[str], *, env: dict[str, str] | None = None) -> Any:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)


def stable_json(document: Any) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def normalize_python_sbom(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document["metadata"]
    metadata.pop("timestamp", None)
    document.pop("serialNumber", None)
    metadata["properties"] = [
        {"name": "ai-platform:source-lock", "value": "uv.lock"},
        {"name": "ai-platform:scope", "value": "production"},
    ]
    return document


def generate_python_sbom() -> dict[str, Any]:
    cache_dir = ROOT / ".ai-platform" / "cache" / "uv"
    cache_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = str(cache_dir)
    document = run_json(
        [
            "uv",
            "export",
            "--locked",
            "--offline",
            "--preview-features",
            "sbom-export",
            "--no-dev",
            "--no-group",
            "ai-validation",
            "--format",
            "cyclonedx1.5",
            "--no-emit-project",
        ],
        env=environment,
    )
    if not isinstance(document, dict):
        raise TypeError("uv 导出的 Python SBOM 顶层必须是对象")
    return normalize_python_sbom(document)


def package_key(name: str, version: str) -> str:
    return f"{name}@{version}"


def npm_purl(name: str, version: str) -> str:
    return f"pkg:npm/{quote(name, safe='')}@{version}"


def collect_node_graph(
    dependencies: dict[str, Any],
    components: dict[str, dict[str, Any]],
    edges: dict[str, set[str]],
) -> set[str]:
    direct_refs: set[str] = set()
    for name, dependency in dependencies.items():
        if not isinstance(dependency, dict) or "version" not in dependency:
            continue
        version = str(dependency["version"])
        ref = package_key(name, version)
        direct_refs.add(ref)
        package_path = Path(str(dependency.get("path", "")))
        manifest_path = package_path / "package.json"
        manifest: dict[str, Any] = {}
        if manifest_path.is_file():
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": npm_purl(name, version),
        }
        license_value = manifest.get("license")
        if isinstance(license_value, str) and license_value:
            component["licenses"] = [{"license": {"id": license_value}}]
        components[ref] = component
        child_dependencies = dependency.get("dependencies", {})
        if isinstance(child_dependencies, dict):
            child_refs = collect_node_graph(child_dependencies, components, edges)
            edges[ref].update(child_refs)
    return direct_refs


def generate_node_sbom() -> dict[str, Any]:
    installed = run_json(["pnpm", "list", "--prod", "--json", "--depth", "Infinity"])
    if not isinstance(installed, list):
        raise TypeError("pnpm 依赖图顶层必须是数组")
    workspace = next(
        (
            item
            for item in installed
            if isinstance(item, dict) and item.get("path") == str(ROOT / NODE_IMPORTER)
        ),
        None,
    )
    if workspace is None:
        raise RuntimeError(f"找不到 {NODE_IMPORTER} 的已安装生产依赖图")
    components: dict[str, dict[str, Any]] = {}
    edges: dict[str, set[str]] = defaultdict(set)
    dependencies = workspace.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise TypeError("前端生产依赖必须是对象")
    root_ref = "@ai-platform/web@0.0.0"
    direct_refs = collect_node_graph(dependencies, components, edges)
    dependency_graph = [{"ref": root_ref, "dependsOn": sorted(direct_refs)}]
    dependency_graph.extend(
        {"ref": ref, "dependsOn": sorted(edges.get(ref, set()))} for ref in sorted(components)
    )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "tools": [{"vendor": "pnpm", "name": "pnpm", "version": "11.20.0"}],
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "@ai-platform/web",
                "version": "0.0.0",
            },
            "properties": [
                {"name": "ai-platform:source-lock", "value": "pnpm-lock.yaml"},
                {"name": "ai-platform:scope", "value": "production"},
            ],
        },
        "components": [components[ref] for ref in sorted(components)],
        "dependencies": dependency_graph,
    }


def python_license(distribution: Distribution) -> str:
    metadata = distribution.metadata
    license_value = metadata.get("License-Expression") or metadata.get("License")
    if license_value and license_value != "UNKNOWN":
        return str(license_value).replace(" License", "")
    classifiers = [
        classifier.removeprefix("License :: OSI Approved :: ").replace(" License", "")
        for classifier in metadata.get_all("Classifier", [])
        if classifier.startswith("License :: OSI Approved :: ")
    ]
    return " OR ".join(classifiers) if classifiers else "UNKNOWN"


def python_licenses(python_sbom: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {
        (str(component["name"]).lower(), str(component["version"]))
        for component in python_sbom["components"]
    }
    records: list[dict[str, Any]] = []
    for distribution in distributions():
        name = str(distribution.metadata.get("Name") or "")
        version = distribution.version
        if (name.lower(), version) not in allowed:
            continue
        records.append(
            {
                "name": name,
                "version": version,
                "license": python_license(distribution),
                "direct": name.lower() in PYTHON_DIRECT,
            }
        )
    return sorted(records, key=lambda record: (record["name"].lower(), record["version"]))


def node_licenses() -> list[dict[str, Any]]:
    document = run_json(["pnpm", "licenses", "list", "--prod", "--json", "--long"])
    if not isinstance(document, dict):
        raise TypeError("pnpm 许可证清单顶层必须是对象")
    records: list[dict[str, Any]] = []
    for license_name, packages in document.items():
        if not isinstance(packages, list):
            continue
        for package in packages:
            for version in package.get("versions", []):
                records.append(
                    {
                        "name": package["name"],
                        "version": version,
                        "license": license_name,
                    }
                )
    return sorted(records, key=lambda record: (record["name"], record["version"]))


def generate_license_inventory(python_sbom: dict[str, Any]) -> dict[str, Any]:
    python_records = python_licenses(python_sbom)
    node_records = node_licenses()
    unknown_python = [record["name"] for record in python_records if record["license"] == "UNKNOWN"]
    return {
        "inventory_version": "p0-12-v1",
        "sources": {
            "python": "uv.lock 与当前冻结 .venv 包元数据",
            "node": "pnpm-lock.yaml 与 pnpm licenses list --prod",
        },
        "summary": {
            "python_packages": len(python_records),
            "node_packages": len(node_records),
            "unknown_python_licenses": unknown_python,
        },
        "python": python_records,
        "node": node_records,
    }


def expected_outputs() -> dict[Path, str]:
    python_sbom = generate_python_sbom()
    return {
        PYTHON_SBOM: stable_json(python_sbom),
        NODE_SBOM: stable_json(generate_node_sbom()),
        LICENSE_INVENTORY: stable_json(generate_license_inventory(python_sbom)),
    }


def stale_outputs(outputs: dict[Path, str]) -> list[Path]:
    """返回缺失或与冻结依赖图不一致的供应链产物。"""
    return [
        path
        for path, content in outputs.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查已提交产物是否与锁文件一致")
    arguments = parser.parse_args()
    outputs = expected_outputs()
    if arguments.check:
        stale = stale_outputs(outputs)
        if stale:
            print("供应链产物需要重新生成:")
            print("\n".join(f"- {path.relative_to(ROOT)}" for path in stale))
            return 1
        print("SBOM 与许可证清单检查通过")
        return 0
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(f"已生成 {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

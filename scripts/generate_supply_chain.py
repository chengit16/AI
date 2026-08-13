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
    "argon2-cffi",
    "celery",
    "cryptography",
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


def node_package_path(node_modules: Path, name: str) -> Path | None:
    candidate = node_modules.joinpath(*name.split("/"))
    return candidate.resolve() if candidate.exists() else None


def package_node_modules(package_path: Path) -> Path:
    for parent in package_path.parents:
        if parent.name == "node_modules":
            return parent
    raise RuntimeError(f"Node 包不位于 node_modules 中: {package_path}")


def collect_node_graph(
    package_paths: dict[str, Path],
    components: dict[str, dict[str, Any]],
    edges: dict[str, set[str]],
    visited_paths: set[Path],
) -> set[str]:
    direct_refs: set[str] = set()
    for expected_name, package_path in package_paths.items():
        manifest_path = package_path / "package.json"
        if package_path in visited_paths or not manifest_path.is_file():
            continue
        visited_paths.add(package_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError(f"Node 包清单顶层必须是对象: {manifest_path}")
        name = str(manifest.get("name") or expected_name)
        version = str(manifest.get("version") or "")
        if not version:
            raise ValueError(f"Node 包缺少版本: {manifest_path}")
        ref = package_key(name, version)
        direct_refs.add(ref)
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
        dependency_names: set[str] = set()
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            declared = manifest.get(field, {})
            if isinstance(declared, dict):
                dependency_names.update(str(dependency_name) for dependency_name in declared)
        node_modules = package_node_modules(package_path)
        children = {
            dependency_name: dependency_path
            for dependency_name in sorted(dependency_names)
            if (dependency_path := node_package_path(node_modules, dependency_name)) is not None
        }
        child_refs = collect_node_graph(children, components, edges, visited_paths)
        edges[ref].update(child_refs)
    return direct_refs


def generate_node_sbom() -> dict[str, Any]:
    workspace = ROOT / NODE_IMPORTER
    workspace_manifest = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    if not isinstance(workspace_manifest, dict):
        raise TypeError(f"{NODE_IMPORTER}/package.json 顶层必须是对象")
    declared_dependencies = workspace_manifest.get("dependencies", {})
    if not isinstance(declared_dependencies, dict):
        raise TypeError("前端生产依赖必须是对象")
    workspace_node_modules = workspace / "node_modules"
    direct_packages = {
        name: package_path
        for dependency_name in sorted(declared_dependencies)
        if (package_path := node_package_path(workspace_node_modules, str(dependency_name)))
        is not None
        for name in [str(dependency_name)]
    }
    if len(direct_packages) != len(declared_dependencies):
        missing = sorted(set(map(str, declared_dependencies)) - set(direct_packages))
        raise RuntimeError(f"缺少已安装的前端生产依赖: {', '.join(missing)}")
    components: dict[str, dict[str, Any]] = {}
    edges: dict[str, set[str]] = defaultdict(set)
    root_ref = "@ai-platform/web@0.0.0"
    direct_refs = collect_node_graph(direct_packages, components, edges, set())
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


def node_licenses(node_sbom: dict[str, Any]) -> list[dict[str, Any]]:
    """从已解析的生产组件生成清单，避免依赖 pnpm Store 的机器本地索引。"""
    records: list[dict[str, Any]] = []
    for component in node_sbom["components"]:
        licenses = component.get("licenses", [])
        license_names = sorted(
            str(entry["license"]["id"])
            for entry in licenses
            if isinstance(entry, dict)
            and isinstance(entry.get("license"), dict)
            and isinstance(entry["license"].get("id"), str)
        )
        records.append(
            {
                "name": str(component["name"]),
                "version": str(component["version"]),
                "license": " OR ".join(license_names) if license_names else "UNKNOWN",
            }
        )
    return sorted(records, key=lambda record: (record["name"], record["version"]))


def generate_license_inventory(
    python_sbom: dict[str, Any],
    node_sbom: dict[str, Any],
) -> dict[str, Any]:
    python_records = python_licenses(python_sbom)
    node_records = node_licenses(node_sbom)
    unknown_python = [record["name"] for record in python_records if record["license"] == "UNKNOWN"]
    return {
        "inventory_version": "p0-12-v1",
        "sources": {
            "python": "uv.lock 与当前冻结 .venv 包元数据",
            "node": "pnpm-lock.yaml、生产依赖图与各包 package.json",
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
    node_sbom = generate_node_sbom()
    return {
        PYTHON_SBOM: stable_json(python_sbom),
        NODE_SBOM: stable_json(node_sbom),
        LICENSE_INVENTORY: stable_json(generate_license_inventory(python_sbom, node_sbom)),
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

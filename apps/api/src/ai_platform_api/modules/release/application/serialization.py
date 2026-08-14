"""在 ReleaseManifest 领域对象与稳定 JSON 文档之间严格转换。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ai_platform_api.modules.release.domain.models import (
    CompatibilityMatrix,
    ComponentRule,
    ComponentVersion,
    DatabaseVersion,
    ImageArtifact,
    ManifestInputs,
    ReleaseManifest,
    RuntimeRule,
    RuntimeVersions,
)

JsonObject = dict[str, object]


def load_json(path: Path) -> JsonObject:
    """加载JSON，并在可信上下文内维持授权、事务与审计边界。"""

    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(JsonObject, loaded)


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{label}必须是对象")
    return cast(JsonObject, value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label}必须是列表")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label}必须是字符串")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label}必须是整数")
    return value


def parse_inputs(document: JsonObject) -> ManifestInputs:
    """解析输入，并在可信上下文内维持授权、事务与审计边界。"""

    database = _object(document.get("database"), "database")
    runtimes = _object(document.get("runtimes"), "runtimes")
    return ManifestInputs(
        release_version=_string(document.get("release_version"), "release_version"),
        compatibility_matrix_version=_string(
            document.get("compatibility_matrix_version"),
            "compatibility_matrix_version",
        ),
        components=tuple(
            ComponentVersion(
                name=_string(item.get("name"), "components.name"),
                version=_string(item.get("version"), "components.version"),
                source_digest=_string(
                    item.get("source_digest"),
                    "components.source_digest",
                ),
            )
            for raw_item in _list(document.get("components"), "components")
            for item in (_object(raw_item, "components[]"),)
        ),
        database=DatabaseVersion(
            schema_revision=_string(database.get("schema_revision"), "database.schema_revision")
        ),
        runtimes=RuntimeVersions(
            node=_string(runtimes.get("node"), "runtimes.node"),
            python=_string(runtimes.get("python"), "runtimes.python"),
        ),
        images=tuple(
            ImageArtifact(
                name=_string(item.get("name"), "images.name"),
                reference=_string(item.get("reference"), "images.reference"),
                digest=_string(item.get("digest"), "images.digest"),
                platforms=tuple(
                    _string(platform, "images.platforms[]")
                    for platform in _list(item.get("platforms"), "images.platforms")
                ),
            )
            for raw_item in _list(document.get("images"), "images")
            for item in (_object(raw_item, "images[]"),)
        ),
    )


def parse_manifest(document: JsonObject) -> ReleaseManifest:
    """解析清单，并在可信上下文内维持授权、事务与审计边界。"""

    inputs = parse_inputs(document)
    return ReleaseManifest(
        schema_version=_integer(document.get("schema_version"), "schema_version"),
        release_version=inputs.release_version,
        compatibility_matrix_version=inputs.compatibility_matrix_version,
        components=tuple(sorted(inputs.components, key=lambda item: item.name)),
        database=inputs.database,
        runtimes=inputs.runtimes,
        images=tuple(sorted(inputs.images, key=lambda item: item.name)),
        manifest_digest=_string(document.get("manifest_digest"), "manifest_digest"),
    )


def parse_matrix(document: JsonObject) -> CompatibilityMatrix:
    """解析矩阵，并在可信上下文内维持授权、事务与审计边界。"""

    return CompatibilityMatrix(
        schema_version=_integer(document.get("schema_version"), "schema_version"),
        matrix_version=_string(document.get("matrix_version"), "matrix_version"),
        manifest_schema_version=_integer(
            document.get("manifest_schema_version"),
            "manifest_schema_version",
        ),
        component_rules=tuple(
            ComponentRule(
                name=_string(item.get("name"), "component_rules.name"),
                minimum_version=_string(
                    item.get("minimum_version"),
                    "component_rules.minimum_version",
                ),
                maximum_exclusive_version=_string(
                    item.get("maximum_exclusive_version"),
                    "component_rules.maximum_exclusive_version",
                ),
            )
            for raw_item in _list(document.get("component_rules"), "component_rules")
            for item in (_object(raw_item, "component_rules[]"),)
        ),
        database_revisions=tuple(
            _string(revision, "database_revisions[]")
            for revision in _list(document.get("database_revisions"), "database_revisions")
        ),
        runtime_rules=tuple(
            RuntimeRule(
                name=_string(item.get("name"), "runtime_rules.name"),
                version_prefix=_string(
                    item.get("version_prefix"),
                    "runtime_rules.version_prefix",
                ),
            )
            for raw_item in _list(document.get("runtime_rules"), "runtime_rules")
            for item in (_object(raw_item, "runtime_rules[]"),)
        ),
        required_images=tuple(
            _string(image, "required_images[]")
            for image in _list(document.get("required_images"), "required_images")
        ),
    )

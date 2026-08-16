"""验证 P1A-01 发布清单确定性和组件兼容矩阵。"""

from dataclasses import replace
from pathlib import Path

from ai_platform_api.modules.release.application.manifest import ReleaseManifestService
from ai_platform_api.modules.release.domain.models import (
    ImageArtifact,
    ManifestInputs,
)

from scripts.generate_release_manifest import load_json, parse_inputs, parse_matrix

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "contracts" / "fixtures" / "release-manifest-input.v1.valid.json"
MATRIX = ROOT / "contracts" / "release" / "compatibility-matrix.v1.json"


def inputs() -> ManifestInputs:
    return parse_inputs(load_json(FIXTURE))


def test_manifest_generation_is_reproducible_and_order_independent() -> None:
    service = ReleaseManifestService()
    original = inputs()
    reordered = replace(
        original,
        components=tuple(reversed(original.components)),
        images=tuple(reversed(original.images)),
    )

    first = service.build(original)
    second = service.build(reordered)

    assert first == second
    assert first.manifest_digest.startswith("sha256:")
    assert [component.name for component in first.components] == [
        "api",
        "contracts",
        "web",
        "worker",
    ]


def test_valid_manifest_matches_frozen_matrix() -> None:
    service = ReleaseManifestService()
    manifest = service.build(inputs())
    matrix = parse_matrix(load_json(MATRIX))

    result = service.validate(manifest, matrix)

    assert result.compatible is True
    assert result.reasons == ()


def test_current_database_revision_is_release_compatible() -> None:
    matrix = parse_matrix(load_json(MATRIX))

    assert matrix.database_revisions[-1] == "20260816_0055"
    assert len(matrix.database_revisions) == len(set(matrix.database_revisions))
    assert tuple(sorted(matrix.database_revisions)) == matrix.database_revisions


def test_manifest_tampering_is_rejected() -> None:
    service = ReleaseManifestService()
    manifest = service.build(inputs())
    tampered = replace(manifest, release_version="0.2.0")

    result = service.validate(tampered, parse_matrix(load_json(MATRIX)))

    assert result.compatible is False
    assert result.reasons == ("MANIFEST_DIGEST_MISMATCH",)


def test_incompatible_combination_reports_all_failures() -> None:
    service = ReleaseManifestService()
    original = inputs()
    incompatible_components = tuple(
        replace(component, version="1.0.0") if component.name == "api" else component
        for component in original.components
    )
    manifest = service.build(
        replace(
            original,
            components=incompatible_components,
            database=replace(original.database, schema_revision="20990101_unknown"),
            runtimes=replace(original.runtimes, node="25.0.0"),
            images=tuple(image for image in original.images if image.name != "worker"),
        )
    )

    result = service.validate(manifest, parse_matrix(load_json(MATRIX)))

    assert result.compatible is False
    assert result.reasons == (
        "COMPONENT_VERSION_INCOMPATIBLE:api",
        "DATABASE_REVISION_INCOMPATIBLE",
        "RUNTIME_VERSION_INCOMPATIBLE:node",
        "IMAGE_MISSING:worker",
    )


def test_mutable_image_tag_still_requires_digest() -> None:
    original = inputs()

    image = ImageArtifact(
        name="api",
        reference="local/ai-platform-api:latest",
        digest="sha256:" + "f" * 64,
        platforms=("linux/amd64", "linux/arm64"),
    )

    assert image.digest == "sha256:" + "f" * 64
    assert original.images

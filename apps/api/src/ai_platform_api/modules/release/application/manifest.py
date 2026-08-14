"""从冻结输入生成可复现 ReleaseManifest 并校验组件兼容性。"""

import hashlib
import json
from dataclasses import replace

from ai_platform_api.modules.release.domain.models import (
    CompatibilityMatrix,
    CompatibilityResult,
    ManifestInputs,
    ReleaseManifest,
    SemanticVersion,
)


class ReleaseManifestService:
    """集中生成发布清单并判断整个运行组合是否兼容。"""

    def build(self, inputs: ManifestInputs) -> ReleaseManifest:
        """从构建输入生成发布清单，固定组件版本、镜像摘要和迁移兼容信息。"""

        unsigned = ReleaseManifest.from_inputs(inputs, manifest_digest="sha256:" + "0" * 64)
        digest = self._document_digest(unsigned.to_dict(include_digest=False))
        return replace(unsigned, manifest_digest=digest)

    def validate(
        self,
        manifest: ReleaseManifest,
        matrix: CompatibilityMatrix,
    ) -> CompatibilityResult:
        """校验发布清单完整性与组件兼容矩阵，不满足基线时阻断启动。"""

        # 1. 先验证内容摘要和清单自身版本，防止被篡改或错误格式继续参与兼容判断。
        reasons: list[str] = []
        expected_digest = self._document_digest(manifest.to_dict(include_digest=False))
        if manifest.manifest_digest != expected_digest:
            reasons.append("MANIFEST_DIGEST_MISMATCH")
        if manifest.schema_version != matrix.manifest_schema_version:
            reasons.append("MANIFEST_SCHEMA_INCOMPATIBLE")
        if manifest.compatibility_matrix_version != matrix.matrix_version:
            reasons.append("COMPATIBILITY_MATRIX_MISMATCH")

        # 2. 逐项验证组件语义版本及数据库 Revision 是否落在兼容矩阵内。
        components = {component.name: component for component in manifest.components}
        for component_rule in matrix.component_rules:
            component = components.get(component_rule.name)
            if component is None:
                reasons.append(f"COMPONENT_MISSING:{component_rule.name}")
                continue
            component_version = SemanticVersion.parse(component.version)
            if not (
                SemanticVersion.parse(component_rule.minimum_version)
                <= component_version
                < SemanticVersion.parse(component_rule.maximum_exclusive_version)
            ):
                reasons.append(f"COMPONENT_VERSION_INCOMPATIBLE:{component_rule.name}")

        if manifest.database.schema_revision not in matrix.database_revisions:
            reasons.append("DATABASE_REVISION_INCOMPATIBLE")

        # 3. 最后验证运行时前缀和必需镜像，聚合全部原因供启动诊断一次展示。
        runtime_versions = manifest.runtimes.as_dict()
        for runtime_rule in matrix.runtime_rules:
            runtime_version = runtime_versions.get(runtime_rule.name)
            if runtime_version is None:
                reasons.append(f"RUNTIME_MISSING:{runtime_rule.name}")
            elif not runtime_version.startswith(runtime_rule.version_prefix):
                reasons.append(f"RUNTIME_VERSION_INCOMPATIBLE:{runtime_rule.name}")

        images = {image.name for image in manifest.images}
        for required_image in matrix.required_images:
            if required_image not in images:
                reasons.append(f"IMAGE_MISSING:{required_image}")
        return CompatibilityResult(compatible=not reasons, reasons=tuple(reasons))

    @staticmethod
    def _document_digest(document: dict[str, object]) -> str:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

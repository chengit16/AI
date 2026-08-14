"""定义发布清单组件版本、兼容矩阵和确定性校验领域模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMANTIC_VERSION_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def require_identifier(value: str, label: str) -> None:
    """处理校验标识，输入违反领域不变量时失败关闭。"""

    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", value):
        raise ValueError(f"{label}必须是稳定的小写标识")


def require_digest(value: str, label: str) -> None:
    """处理校验摘要，输入违反领域不变量时失败关闭。"""

    if not DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{label}必须是 sha256 摘要")


@total_ordering
@dataclass(frozen=True)
class SemanticVersion:
    """解析和比较不带预发布标签的主、次、补丁版本。"""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = SEMANTIC_VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"版本不符合 SemVer: {value}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        if any(item.isdigit() and len(item) > 1 and item.startswith("0") for item in prerelease):
            raise ValueError(f"SemVer 数字预发布标识不能包含前导零: {value}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        stable = (self.major, self.minor, self.patch)
        other_stable = (other.major, other.minor, other.patch)
        if stable != other_stable:
            return stable < other_stable
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class ComponentVersion:
    """记录发布组件名称、语义版本和构建来源。"""

    name: str
    version: str
    source_digest: str

    def __post_init__(self) -> None:
        require_identifier(self.name, "组件名称")
        SemanticVersion.parse(self.version)
        require_digest(self.source_digest, "组件源码")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version, "source_digest": self.source_digest}


@dataclass(frozen=True)
class ImageArtifact:
    """记录容器镜像名称和不可变 SHA-256 摘要。"""

    name: str
    reference: str
    digest: str
    platforms: tuple[str, ...]

    def __post_init__(self) -> None:
        require_identifier(self.name, "镜像名称")
        require_digest(self.digest, "镜像")
        if not self.reference.strip():
            raise ValueError("镜像引用不能为空")
        if not self.platforms or len(self.platforms) != len(set(self.platforms)):
            raise ValueError("镜像平台必须非空且不能重复")
        if not set(self.platforms).issubset({"linux/amd64", "linux/arm64"}):
            raise ValueError("镜像平台不在当前支持范围")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "reference": self.reference,
            "digest": self.digest,
            "platforms": list(self.platforms),
        }


@dataclass(frozen=True)
class DatabaseVersion:
    """记录数据库类型、Schema Revision 和最低兼容版本。"""

    schema_revision: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.schema_revision):
            raise ValueError("数据库 Revision 无效")


@dataclass(frozen=True)
class RuntimeVersions:
    """固定 Node.js、Python 及后置 Go 运行层接口版本。"""

    node: str
    python: str

    def __post_init__(self) -> None:
        SemanticVersion.parse(self.node)
        SemanticVersion.parse(self.python)

    def as_dict(self) -> dict[str, str]:
        return {"node": self.node, "python": self.python}


@dataclass(frozen=True)
class ManifestInputs:
    """汇总生成发布清单所需的组件、镜像、数据库和运行时事实。"""

    release_version: str
    compatibility_matrix_version: str
    components: tuple[ComponentVersion, ...]
    database: DatabaseVersion
    runtimes: RuntimeVersions
    images: tuple[ImageArtifact, ...]

    def __post_init__(self) -> None:
        SemanticVersion.parse(self.release_version)
        if not self.compatibility_matrix_version.strip():
            raise ValueError("兼容矩阵版本不能为空")
        if not self.components or not self.images:
            raise ValueError("发布清单必须包含组件和镜像")
        if len({item.name for item in self.components}) != len(self.components):
            raise ValueError("发布组件名称不能重复")
        if len({item.name for item in self.images}) != len(self.images):
            raise ValueError("发布镜像名称不能重复")


@dataclass(frozen=True)
class ReleaseManifest:
    """提供内容寻址的完整发布组合，作为本地启动兼容判断依据。"""

    schema_version: int
    release_version: str
    compatibility_matrix_version: str
    components: tuple[ComponentVersion, ...]
    database: DatabaseVersion
    runtimes: RuntimeVersions
    images: tuple[ImageArtifact, ...]
    manifest_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("当前只支持 ReleaseManifest V1")
        SemanticVersion.parse(self.release_version)
        require_digest(self.manifest_digest, "发布清单")

    @classmethod
    def from_inputs(cls, inputs: ManifestInputs, manifest_digest: str) -> ReleaseManifest:
        return cls(
            schema_version=1,
            release_version=inputs.release_version,
            compatibility_matrix_version=inputs.compatibility_matrix_version,
            components=tuple(sorted(inputs.components, key=lambda item: item.name)),
            database=inputs.database,
            runtimes=inputs.runtimes,
            images=tuple(sorted(inputs.images, key=lambda item: item.name)),
            manifest_digest=manifest_digest,
        )

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        document: dict[str, object] = {
            "schema_version": self.schema_version,
            "release_version": self.release_version,
            "compatibility_matrix_version": self.compatibility_matrix_version,
            "components": [component.to_dict() for component in self.components],
            "database": {"schema_revision": self.database.schema_revision},
            "runtimes": self.runtimes.as_dict(),
            "images": [image.to_dict() for image in self.images],
        }
        if include_digest:
            document["manifest_digest"] = self.manifest_digest
        return document


@dataclass(frozen=True)
class ComponentRule:
    """定义组件允许的最小版本和排他最大版本。"""

    name: str
    minimum_version: str
    maximum_exclusive_version: str

    def __post_init__(self) -> None:
        require_identifier(self.name, "组件规则名称")
        minimum = SemanticVersion.parse(self.minimum_version)
        maximum = SemanticVersion.parse(self.maximum_exclusive_version)
        if minimum >= maximum:
            raise ValueError("组件版本范围必须满足最小版本小于最大排除版本")


@dataclass(frozen=True)
class RuntimeRule:
    """定义指定运行时必须匹配的版本前缀。"""

    name: str
    version_prefix: str

    def __post_init__(self) -> None:
        if self.name not in {"node", "python"}:
            raise ValueError("运行时规则名称无效")
        if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.", self.version_prefix):
            raise ValueError("运行时版本前缀必须固定主次版本")


@dataclass(frozen=True)
class CompatibilityMatrix:
    """汇总清单版本、组件范围、数据库 Revision、运行时和必需镜像规则。"""

    schema_version: int
    matrix_version: str
    manifest_schema_version: int
    component_rules: tuple[ComponentRule, ...]
    database_revisions: tuple[str, ...]
    runtime_rules: tuple[RuntimeRule, ...]
    required_images: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.manifest_schema_version != 1:
            raise ValueError("当前只支持兼容矩阵和发布清单 V1")
        if not self.matrix_version.strip():
            raise ValueError("兼容矩阵版本不能为空")
        if not self.component_rules or not self.database_revisions or not self.required_images:
            raise ValueError("兼容矩阵的组件、数据库和镜像规则不能为空")
        if len({rule.name for rule in self.component_rules}) != len(self.component_rules):
            raise ValueError("组件兼容规则不能重复")
        if len({rule.name for rule in self.runtime_rules}) != len(self.runtime_rules):
            raise ValueError("运行时兼容规则不能重复")


@dataclass(frozen=True)
class CompatibilityResult:
    """返回组合是否可启动及全部稳定不兼容原因。"""

    compatible: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.compatible == bool(self.reasons):
            raise ValueError("兼容结论与原因不一致")

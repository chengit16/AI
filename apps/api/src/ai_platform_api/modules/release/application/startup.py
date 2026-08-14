"""在应用开放服务前加载并验证完整发布组合。"""

import json
from pathlib import Path

from ai_platform_api.modules.release.application.manifest import ReleaseManifestService
from ai_platform_api.modules.release.application.serialization import (
    load_json,
    parse_manifest,
    parse_matrix,
)
from ai_platform_api.modules.release.domain.errors import (
    ReleaseCombinationIncompatibleError,
    ReleaseManifestInvalidError,
)


def verify_release_compatibility(manifest_path: str, matrix_path: str) -> None:
    """应用开放服务前验证完整运行组合，避免不兼容组件进入半可用状态。"""

    try:
        manifest = parse_manifest(load_json(Path(manifest_path)))
        matrix = parse_matrix(load_json(Path(matrix_path)))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ReleaseManifestInvalidError from error

    result = ReleaseManifestService().validate(manifest, matrix)
    if not result.compatible:
        raise ReleaseCombinationIncompatibleError(result.reasons)

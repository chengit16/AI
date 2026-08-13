"""创建带稳定校验和的正式发布供应链归档目录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_FILES = (
    ROOT / "docs" / "supply-chain" / "python-production.cdx.json",
    ROOT / "docs" / "supply-chain" / "node-production.cdx.json",
    ROOT / "docs" / "supply-chain" / "dependency-licenses.json",
    ROOT / "apps" / "web" / "src" / "api" / "generated" / "platform-api.v1.ts",
    ROOT / "packages" / "contracts" / "src" / "ai_platform_contracts" / "platform_api_v1.py",
)


def require_release_passed(readiness: Path) -> None:
    document = json.loads(readiness.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("供应链门禁状态 Schema 无效")
    if document.get("release_status") != "passed":
        raise ValueError("正式发布门禁尚未通过。拒绝创建发布归档")


def create_bundle(
    *,
    manifest: Path,
    readiness: Path,
    output: Path,
    files: tuple[Path, ...] = DEFAULT_FILES,
) -> None:
    require_release_passed(readiness)
    inputs = (manifest, readiness, *files)
    missing = [path for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"发布归档缺少输入: {', '.join(map(str, missing))}")
    if output.exists():
        raise FileExistsError(f"发布归档目录已存在: {output}")
    output.mkdir(parents=True)
    copied: list[Path] = []
    for source in inputs:
        target = output / source.name
        if target.exists():
            raise ValueError(f"发布归档文件名冲突: {source.name}")
        shutil.copyfile(source, target)
        copied.append(target)
    checksums = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in sorted(copied)
    ]
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, required=True, help="真实构建生成的 ReleaseManifest"
    )
    parser.add_argument("--readiness", type=Path, required=True, help="发布门禁状态清单")
    parser.add_argument("--output", type=Path, required=True, help="必须尚不存在的归档目录")
    arguments = parser.parse_args()
    try:
        create_bundle(
            manifest=arguments.manifest,
            readiness=arguments.readiness,
            output=arguments.output,
        )
    except (FileExistsError, FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(str(error))
        return 1
    print(f"已创建正式发布供应链归档: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

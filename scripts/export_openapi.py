"""从 FastAPI 应用工厂导出冻结 OpenAPI，供契约与资源注册表门禁使用。"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path[:0] = [
    str(ROOT / "apps/api/src"),
    str(ROOT / "packages/backend/src"),
]

from ai_platform_api.main import app  # noqa: E402

OUTPUT = ROOT / "contracts/openapi/platform-api.v1.json"


def rendered() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查冻结契约是否与实现一致")
    arguments = parser.parse_args()
    content = rendered()
    if arguments.check:
        with tempfile.TemporaryDirectory(prefix="ai-platform-openapi-") as directory:
            candidate = Path(directory) / OUTPUT.name
            candidate.write_text(content, encoding="utf-8")
            if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
                print(f"OpenAPI 冻结契约需要更新: {OUTPUT.relative_to(ROOT)}")
                return 1
        print("OpenAPI 冻结契约与 FastAPI 实现一致")
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"已生成 OpenAPI 冻结契约: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

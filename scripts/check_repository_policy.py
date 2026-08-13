"""检查已跟踪文件中的高风险凭证与敏感文件路径。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519", "master.key"}
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat|afxp)_[A-Za-z0-9_-]{20,}\b"),
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def collect_violations(paths: list[Path] | None = None) -> list[str]:
    violations: list[str] = []
    for path in paths or tracked_files():
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            relative = Path(path.name)
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"禁止跟踪敏感文件: {relative}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                violations.append(f"发现疑似真实凭证: {relative}")
                break
    return violations


def main() -> int:
    violations = collect_violations()
    if not violations:
        print("仓库敏感文件与凭证模式检查通过")
        return 0
    print("\n".join(violations))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

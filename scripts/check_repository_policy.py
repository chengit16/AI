"""检查当前仓库与完整 Git 历史中的高风险凭证。"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519", "master.key"}
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat|afxp)_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
    re.compile(r"\bsk_live_[0-9A-Za-z]{20,}\b"),
)


def tracked_files(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode() for item in result.stdout.split(b"\0") if item]


def collect_violations(paths: list[Path] | None = None, *, root: Path = ROOT) -> list[str]:
    violations: list[str] = []
    for path in paths or tracked_files(root):
        try:
            relative = path.relative_to(root)
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


def history_blobs(root: Path = ROOT) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    candidates: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        object_id, separator, path = line.partition(" ")
        if separator and path:
            candidates.append((object_id, path))
    if not candidates:
        return []
    check = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        cwd=root,
        input="".join(f"{object_id}\n" for object_id, _ in candidates),
        check=True,
        capture_output=True,
        text=True,
    )
    object_types = {
        line.split(" ", maxsplit=1)[0]: line.split(" ", maxsplit=1)[1]
        for line in check.stdout.splitlines()
    }
    return [
        (object_id, path) for object_id, path in candidates if object_types[object_id] == "blob"
    ]


def collect_history_violations(root: Path = ROOT) -> list[str]:
    violations: list[str] = []
    seen: set[tuple[str, str]] = set()
    for object_id, relative_name in history_blobs(root):
        path = Path(relative_name)
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violation = (object_id, relative_name)
            if violation not in seen:
                violations.append(f"Git 历史包含敏感文件: {object_id[:12]}:{relative_name}")
                seen.add(violation)
            continue
        content = subprocess.run(
            ["git", "cat-file", "blob", object_id],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8", errors="ignore")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            violation = (object_id, relative_name)
            if violation not in seen:
                violations.append(f"Git 历史发现疑似真实凭证: {object_id[:12]}:{relative_name}")
                seen.add(violation)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("current", "history", "all"),
        default="all",
        help="扫描当前文件、Git 历史或二者",
    )
    arguments = parser.parse_args()
    violations: list[str] = []
    if arguments.scope in {"current", "all"}:
        violations.extend(collect_violations())
    if arguments.scope in {"history", "all"}:
        violations.extend(collect_history_violations())
    if not violations:
        print(f"仓库 Secret Scanner 检查通过: scope={arguments.scope}")
        return 0
    print("\n".join(violations))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

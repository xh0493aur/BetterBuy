"""上传前自检：确认这个目录里没有密钥、个人信息和本地路径。

用途（在 push 之前跑，10 秒出结果）::

    python scripts/scan_secrets.py              # 扫工作区
    python scripts/scan_secrets.py --history    # 连 git 历史一起扫
    python scripts/scan_secrets.py --tracked     # 只扫 git 已跟踪的文件

退出码：
    0  干净（可能只有 warn）
    1  有 error 级命中 —— **不要 push**
    2  运行环境有问题（不是 git 仓库却要求扫历史等）

它只读文件、不联网、不写任何东西。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betterbuy.secretscan import Finding, is_never_track, scan_files, scan_text  # noqa: E402

MAX_HISTORY_BYTES = 40 * 1024 * 1024


def _git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"git 调用失败：{exc}"
    return proc.returncode, proc.stdout or ""


def tracked_files() -> list[Path] | None:
    """git 已跟踪的文件（相对路径转绝对）。不是 git 仓库时返回 None。"""
    code, out = _git("ls-files")
    if code != 0:
        return None
    files = [ROOT / line.strip() for line in out.splitlines() if line.strip()]
    return [path for path in files if path.is_file()]


def scan_history() -> list[Finding] | None:
    """扫 git 历史里的补丁文本，抓"曾经提交过、后来删了"的密钥。

    返回 ``None`` 表示这个仓库还没有任何提交，历史无从扫描（不是"扫过且干净"）。
    """
    if _git("rev-parse", "HEAD")[0] != 0:
        return None
    code, out = _git("log", "--all", "-p", "--pretty=format:=== %H %s")
    if code != 0:
        return None
    if len(out.encode("utf-8", errors="ignore")) > MAX_HISTORY_BYTES:
        out = out[-MAX_HISTORY_BYTES:]
    return scan_text(out, "<git-history>")


def report(findings: list[Finding], *, scope: str) -> int:
    errors = [f for f in findings if f.is_error]
    warns = [f for f in findings if not f.is_error]

    print(f"\n=== 扫描范围：{scope} ===")
    if not findings:
        print("  干净：没有发现密钥、个人信息或本地路径。")
        return 0

    if errors:
        print(f"  ✗ error {len(errors)} 条（必须处理，否则不要 push）")
        for finding in errors[:40]:
            print(f"    {finding}")
        if len(errors) > 40:
            print(f"    …还有 {len(errors) - 40} 条")
    if warns:
        print(f"  ! warn {len(warns)} 条（人工确认即可）")
        for finding in warns[:20]:
            print(f"    {finding}")
        if len(warns) > 20:
            print(f"    …还有 {len(warns) - 20} 条")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上传前密钥自检")
    parser.add_argument("--history", action="store_true", help="同时扫描 git 历史补丁")
    parser.add_argument("--tracked", action="store_true", help="只扫 git 已跟踪的文件")
    args = parser.parse_args(argv)

    print(f"目录：{ROOT}")

    paths = None
    scope = "工作区全部文本文件"
    if args.tracked:
        found = tracked_files()
        if found is None:
            print("不是 git 仓库，无法按跟踪文件扫描；改为扫工作区。")
        else:
            paths, scope = found, f"git 已跟踪文件（{len(found)} 个）"

    findings = scan_files(ROOT, paths)
    status = report(findings, scope=scope)

    # 被跟踪的 .env / secrets.toml / *.sqlite3 单独提示（即使内容看起来无害）
    if paths:
        bad = [p for p in paths if is_never_track(str(p))]
        if bad:
            print(f"\n  ✗ 这些文件不该被 git 跟踪：")
            for path in bad:
                print(f"    {path.relative_to(ROOT)}  →  git rm --cached \"{path.name}\"")
            status = 1

    if args.history:
        history = scan_history()
        if history is None:
            print("\n=== 扫描范围：git 历史补丁 ===\n  （这个仓库还没有提交，跳过）")
        else:
            status = max(status, report(history, scope="git 历史补丁"))

    print("\n结论：" + ("发现问题，先处理后再说 push。" if status else "可以上传。"))
    return status


if __name__ == "__main__":
    sys.exit(main())

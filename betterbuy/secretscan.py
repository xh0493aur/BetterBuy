"""上传前自检：在这个仓库里找"不该出现在公开代码里"的东西。

检测分两档：
    ``error``  —— 真的泄漏了（形如 ``APP_SECRET = "abcd1234..."`` 的字面量、
                  ``sk-`` 开头的密钥、被 git 跟踪的 ``.env`` / ``secrets.toml`` /
                  ``*.sqlite3``）。有任一命中就**不该 push**。
    ``warn``   —— 可疑但常见（32 位十六进制串、Windows 绝对路径、邮箱、手机号）。
                 可能只是测试用的固定样例，人工看一眼即可。

设计上刻意**只做文本匹配、不联网、不上传任何内容**——自检工具本身不能成为风险点。
逻辑放在 ``betterbuy`` 里而不是 ``scripts`` 里，是为了能被单元测试直接导入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Finding", "RULES", "NEVER_TRACK", "NEVER_TRACK_SUFFIX", "TEMPLATE_SUFFIX",
    "BINARY_SUFFIXES",
    "scan_text", "is_never_track", "iter_files", "scan_files", "looks_like_placeholder",
]

#: 简单正则规则：``(severity, pattern, 说明)``
RULES: tuple[tuple[str, str, str], ...] = (
    # 常见大模型密钥前缀
    ("error", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}", "看起来是模型 API Key"),
    # 阿里云 / 腾讯云风格 AK
    ("error", r"\b(?:LTAI[A-Za-z0-9]{12,}|AKID[A-Za-z0-9]{13,})\b", "看起来是云厂商 AccessKey"),
    # 私钥块
    ("error", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", "私钥块"),
    # 32 位十六进制（可能是 MD5 样例，也可能是某个 token）
    ("warn", r"\b[0-9a-f]{32}\b", "32 位十六进制串，确认不是 token"),
    # 40 位以上、含大小写和数字的串（token 常见形态）
    ("warn", r"\b(?=[A-Za-z0-9]{40,}\b)(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]+\b",
     "40 位混合串，确认不是 token"),
    # Windows 绝对路径（Linux 云端跑不通，也暴露本机目录结构）
    ("warn", r"\b[A-Za-z]:[\\/][\w\-./\\ ]{3,}", "Windows 绝对路径"),
    # 中国大陆手机号
    ("warn", r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    # 邮箱
    ("warn", r"\b[\w.\-+]+@[\w\-]+\.[A-Za-z]{2,}\b", "邮箱"),
)

#: 变量名像凭据 + 等号右边是带引号的字面量
ASSIGN_RE = re.compile(
    r"""(?i)\b(
        app_?key | app_?secret | client_?secret | client_?id | access_?token |
        api_?key | api_?secret | secret_?key | password | passwd | token
    )\s*[:=]\s*["']([^"']{8,})["']""",
    re.VERBOSE,
)

#: 这些字样的行大概率是文档/示例，不当成泄漏
SAFE_LINE_HINTS: tuple[str, ...] = (
    "os.environ", "os.getenv", "getenv(", "st.secrets", "environ[",
    "example", "placeholder", "your_", "xxx", "****", "填", "示例", "例如", "<",
)

#: 占位值特征（值本身就能看出不是真密钥）
PLACEHOLDER_HINTS: tuple[str, ...] = ("your", "xxx", "填", "示例", "example", "changeme",
                                     "...", "***", "todo", "none", "null", "secret_here",
                                     "在这里")

#: 这些文件名一律不该被 git 跟踪（只要出现就是 error，内容都不看）
NEVER_TRACK: tuple[str, ...] = (".env", "secrets.toml", "id_rsa", "id_ed25519")

#: 同上，按后缀判断
NEVER_TRACK_SUFFIX: tuple[str, ...] = (
    ".pem", ".key", ".p12", ".pfx", ".sqlite3", ".sqlite", ".db", ".db3",
)

#: 二进制文件不读内容
BINARY_SUFFIXES: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".gz", ".whl", ".pyc", ".woff", ".woff2", ".ttf", ".mp4", ".xlsx", ".docx",
)

_COMPILED = tuple((sev, re.compile(pat), note) for sev, pat, note in RULES)


@dataclass(frozen=True)
class Finding:
    """一条命中。``excerpt`` 已把可能的密钥打码，避免报告本身又抄一遍。"""

    path: str
    line: int
    severity: str          # "error" | "warn"
    rule: str
    excerpt: str

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def __str__(self) -> str:
        mark = "✗" if self.severity == "error" else "!"
        return f"{mark} {self.path}:{self.line}  [{self.rule}]  {self.excerpt}"


def looks_like_placeholder(value: str) -> bool:
    """值是否明显是占位符而不是真密钥。"""
    text = value.strip()
    if not text:
        return True
    low = text.lower()
    if any(hint in low for hint in PLACEHOLDER_HINTS):
        return True
    # 全是同一个字符或星号
    if len(set(text)) <= 2:
        return True
    return False


def _mask(text: str, start: int = 0, end: int | None = None) -> str:
    """把 ``[start:end)`` 这段打码，其余原样返回。"""
    if end is None or end <= start:
        return text
    secret = text[start:end]
    if len(secret) <= 6:
        masked = "*" * len(secret)
    else:
        masked = f"{secret[:2]}***{secret[-2:]}"
    return f"{text[:start]}{masked}{text[end:]}"


def _clip(text: str, width: int = 120) -> str:
    flat = text.strip().replace("\t", " ")
    return flat if len(flat) <= width else flat[:width] + "…"


#: 模板文件：虽然是 ``.env*`` 形状，但**应该**提交（值留空，给人抄格式用）
TEMPLATE_SUFFIX: tuple[str, ...] = (".example", ".sample", ".template")


def is_never_track(path: str) -> bool:
    """文件名是否属于"无论内容如何都不该提交"的那类。"""
    name = Path(path).name
    low = name.lower()
    if low in NEVER_TRACK:
        return True
    if low.startswith(".env.") and not low.endswith(TEMPLATE_SUFFIX):
        return True
    return any(low.endswith(suffix) for suffix in NEVER_TRACK_SUFFIX)


def scan_text(text: str, path: str = "") -> list[Finding]:
    """扫描一段文本（纯函数，便于测试）。同一行只报一条，避免刷屏。"""
    if is_never_track(path):
        return [Finding(path, 1, "error", "never-track",
                        "这类文件不应被 git 跟踪（含密钥或本地数据）")]

    findings: list[Finding] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw[:4000]
        if not line.strip():
            continue
        lowered = line.lower()
        # 文档/示例行只保留 error 级判断，跳过 warn 噪声
        doc_line = any(hint in lowered for hint in SAFE_LINE_HINTS)

        hit: Finding | None = None

        # 1) 变量名像凭据 + 字面量赋值
        for match in ASSIGN_RE.finditer(line):
            value = match.group(2)
            if looks_like_placeholder(value):
                continue
            if any(hint in lowered for hint in ("os.environ", "getenv(", "st.secrets")):
                continue
            hit = Finding(path, number, "error", "credential-literal",
                          _clip(_mask(line, match.start(2), match.end(2))))
            break

        # 2) 简单正则规则
        if hit is None:
            for severity, regex, note in _COMPILED:
                if doc_line and severity == "warn":
                    continue
                match = regex.search(line)
                if not match:
                    continue
                hit = Finding(path, number, severity, note,
                              _clip(_mask(line, match.start(), match.end())))
                break

        if hit is not None:
            findings.append(hit)
    return findings


def iter_files(root: str | Path, extra_skip: tuple[str, ...] = ()) -> list[Path]:
    """遍历目录下的文本文件，跳过缓存、虚拟环境、二进制。"""
    base = Path(root)
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules",
                 ".pytest_cache", ".mypy_cache", ".idea", ".vscode"}
    skip_dirs.update(extra_skip)
    out: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
        except OSError:
            continue
        out.append(path)
    return out


def scan_files(root: str | Path, paths: list[Path] | None = None) -> list[Finding]:
    """扫描一组文件（默认扫整个目录）。读不了的文件跳过，不抛异常。"""
    base = Path(root)
    findings: list[Finding] = []
    for path in (paths if paths is not None else iter_files(base)):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            rel = str(path)
        findings.extend(scan_text(text, rel))
    return findings

"""lib/skill_scan.py — 技能安全扫描: 安装 / 草稿启用前扫 SKILL.md + 包内全部文本文件。

规则分两级:
  block — 拒装 (删根目录/家目录、下载即执行、解码即执行、混淆 eval、窃取 SSH 私钥、提示注入、zip 路径穿越)
  warn  — 需用户勾选确认 (读配置/.env/API key 环境变量、往外部地址 POST、crontab/systemd 持久化 ...)
带否定词的说明行 ("不要 rm -rf /", "never run ...") 降为 warn: 技能里写"别这么做"很常见。
返回 {"level": "ok"|"warn"|"block", "findings": [{rule, level, title, file, line, snippet}]}。
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MAX_FILES = 500
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".json", ".yaml",
            ".yml", ".toml", ".ini", ".cfg", ".ps1", ".bat", ".cmd", ".rb", ".pl", ".php", ".html", ".css",
            ".xml", ".sql", ".r", ".lua", ".go", ".rs", ".java", ".env", ""}

_LOCAL = r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\$\{?\w*(?:HOST|URL)\w*\}?)"
_NEGATION = re.compile(r"不要|别|禁止|切勿|不可|不能|避免|严禁|危险|不执行|不照做|不遵守|只是数据|警告|"
                       r"never|don'?t|do not|avoid|dangerous|❌|⛔", re.I)

# (rule, level, 中文说明, 正则)
RULES = [
    ("rm_rf_root", "block", "删除根目录或家目录",
     r"\brm\s+(?:-[a-zA-Z]*\s+)*-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)[a-zA-Z]*\s+(?:--no-preserve-root\s+)?"
     r"(?:/|~|\$HOME|\$\{HOME\})(?:/?\*?)?(?=\s|$|[;&|'\"])"),
    ("pipe_to_shell", "block", "下载内容直接交给 shell 执行",
     r"\b(?:curl|wget)\b[^\n|;]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b"
     r"|\b(?:ba|z)?sh\s+(?:-c\s+)?[\"']?\$\(\s*(?:curl|wget)\b|\b(?:ba|z)?sh\s+<\(\s*(?:curl|wget)\b"
     r"|\b(?:iex|Invoke-Expression)\b[^\n]*(?:DownloadString|Invoke-WebRequest|iwr)\b"),
    ("base64_exec", "block", "解码后直接执行",
     r"base64\s+(?:-d|--decode|-D)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"
     r"|\bexec\s*\(\s*(?:base64\.b64decode|codecs\.decode|zlib\.decompress|bytes\.fromhex|marshal\.loads)"),
    ("eval_obfuscated", "block", "eval 执行混淆/编码内容",
     r"\beval\s*\(\s*(?:atob|unescape|String\.fromCharCode|base64\.b64decode|codecs\.decode|zlib\.decompress"
     r"|bytes\.fromhex|Buffer\.from\([^)]*['\"]base64)"),
    ("ssh_keys", "block", "读取/拷走 SSH 私钥",
     r"\b(?:cat|less|more|head|tail|cp|scp|rsync|base64|xxd|tar|zip|open|read_text|readFile|readFileSync|"
     r"Get-Content)\b[^\n]*(?:\.ssh/id_|\bid_(?:rsa|ed25519|ecdsa|dsa)\b(?!\.pub)|\.ssh/?\s*$|\.ssh/\*)"),
    ("ssh_dir", "warn", "涉及 ~/.ssh 目录",
     r"~/\.ssh\b|\$HOME/\.ssh|/\.ssh/authorized_keys"),
    ("prompt_injection", "block", "提示注入 (要求忽略之前的指令)",
     r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|preceding)"
     r"\s+(?:instructions|prompts|rules|messages)"
     r"|忽略(?:掉)?(?:你)?(?:之前|以上|前面|先前|上面|此前)(?:的|所有的?)*(?:所有)?(?:指令|指示|提示|规则|要求|设定)"
     r"|you\s+are\s+now\s+(?:DAN|in\s+developer\s+mode|jailbroken)"),
    ("fork_bomb", "block", "破坏性命令 (fork 炸弹 / 格式化磁盘)",
     r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:|\bmkfs(?:\.\w+)?\s+/dev/|\bdd\s+[^\n]*of=/dev/(?:sd|nvme|hd|xvd)"),
    ("read_secrets", "warn", "读取配置/密钥文件 (config.json / .env / shadow)",
     r"(?<![\w-])config\.json\b|(?<![\w.])\.env\b(?!\w)|/etc/shadow|\.aws/credentials|\.netrc\b|\.git-credentials"),
    ("secret_env", "warn", "读取 API Key / Token 类环境变量",
     r"\$\{?[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*\}?"
     r"|os\.environ(?:\.get)?[\[(]\s*['\"][A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)"
     r"|process\.env\.[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)"),
    ("exfil_post", "warn", "向外部地址发送数据 (POST/上传)",
     rf"\bcurl\b(?=[^\n]*(?:\s-d\b|--data|\s-F\b|--form|-X\s*POST|--upload-file|\s-T\s))(?![^\n]*{_LOCAL})[^\n]*https?://"
     rf"|\b(?:requests|httpx|axios)\.(?:post|put)\s*\(\s*[f]?['\"]https?://(?!{_LOCAL})"
     rf"|\bfetch\s*\(\s*[`'\"]https?://(?!{_LOCAL})[^\n]*method\s*:\s*['\"](?:POST|PUT)"),
    ("persistence", "warn", "开机自启/定时任务持久化 (crontab / systemd / 启动项)",
     r"\bcrontab\s+(?:-[a-z]*\s+)*(?:-e|-l\s*\||-\s|[^\s-])|/etc/cron|/var/spool/cron|\bsystemctl\s+(?:--user\s+)?enable\b"
     r"|/etc/systemd/system/|\.config/systemd/user|>>\s*~/\.(?:bashrc|profile|zshrc|bash_profile)"
     r"|\blaunchctl\s+load\b|\bschtasks\s+/create\b|CurrentVersion\\\\?Run\b"),
    ("shell_eval", "warn", "eval 执行命令替换的结果",
     r"\beval\s+[\"']?\$\("),
]
_COMPILED = [(r, lv, t, re.compile(p, re.I)) for r, lv, t, p in RULES]
_RANK = {"ok": 0, "warn": 1, "block": 2}


def _finding(rule, level, title, file, line, snippet):
    return {"rule": rule, "level": level, "title": title, "file": file, "line": line,
            "snippet": snippet.strip()[:160]}


def scan_text(text: str, file: str = "SKILL.md") -> list[dict]:
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        if len(ln) > 4000:
            ln = ln[:4000]
        for rule, level, title, rx in _COMPILED:
            if not rx.search(ln):
                continue
            lv = level
            if lv == "block" and _NEGATION.search(ln):
                lv = "warn"
            out.append(_finding(rule, lv, title, file, i, ln))
    return out


def _is_text(p: Path, head: bytes) -> bool:
    if p.suffix.lower() not in TEXT_EXT and p.name.lower() not in {"makefile", "dockerfile"}:
        return False
    return b"\x00" not in head[:4096]


def summarize(findings: list[dict]) -> dict:
    level = "ok"
    for f in findings:
        if _RANK[f["level"]] > _RANK[level]:
            level = f["level"]
    return {"level": level, "findings": findings}


def scan_dir(root: Path) -> dict:
    """扫描一个技能目录 (SKILL.md + scripts/ 等全部文本文件)。"""
    root = Path(root)
    findings: list[dict] = []
    files = sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    for p in files[:MAX_FILES]:
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            findings.append(_finding("symlink", "block", "包内含符号链接 (可能指向包外)", rel, 0, rel))
            continue
        try:
            raw = p.read_bytes()[:MAX_FILE_BYTES]
        except OSError:
            continue
        if not _is_text(p, raw):
            continue
        findings.extend(scan_text(raw.decode("utf-8", errors="replace"), rel))
    if len(files) > MAX_FILES:
        findings.append(_finding("too_many_files", "warn", f"文件过多, 只扫了前 {MAX_FILES} 个", "", 0, ""))
    return summarize(findings)


def zip_traversal_finding(entry: str) -> dict:
    return _finding("zip_traversal", "block", "压缩包路径穿越 (含 .. 或绝对路径)", entry, 0, entry)

"""lib/wechat_media.py — 微信桥媒体/文档解析工具函数（从 wechat_bridge.py 抽取, P2-3）
CDN 下载 / AES 解密 / 图片视觉描述 / PDF·Word·Excel·PPT·文本·压缩包·音频信息提取。
上层调度逻辑 (_preprocess_attachment 等) 仍留在 wechat_bridge.py。纯搬运，逻辑未变。
"""
import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import aiohttp

log = logging.getLogger("wechat_bridge")

_BASE = Path(__file__).parent.parent


def _load_cfg() -> dict:
    p = _BASE / "config.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


# ── SDK CDN 媒体下载（正确方式）────────────────────────────────
async def _sdk_download_media(bot, media_item, ext: str = "bin",
                               filename: str = "", bot_id: str = "",
                               fallback_url: str = "") -> Optional[str]:
    """
    通过 wechatbot-sdk 的 download_raw() 下载媒体文件。
    微信 CDN 使用 encrypt_query_param 加密参数，必须走 SDK，不能裸 HTTP GET。

    优先级：
      1. bot.download_raw(media_item.media, media_item.aes_key)  ← SDK 正规方式
      2. bot.download_raw(media_item, ...)                        ← media_item 本身就是 CDNMedia
      3. fallback: 裸 HTTP GET fallback_url（兜底，大概率失败）
    """
    cfg = _load_cfg()
    dl_timeout = cfg.get("wechat", {}).get("download_timeout", 120)

    if not filename:
        filename = f"wx_{bot_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}.{ext}"
    else:
        filename = re.sub(r'[^\w.\-]', '_', filename)

    data = None

    # ── 方式1: media_item 有 .media（CDNMedia 对象）──
    cdn_media = getattr(media_item, "media", None)
    aes_key   = getattr(media_item, "aes_key", None)
    if cdn_media and bot:
        for attempt in range(2):          # 最多尝试2次
            try:
                data = await asyncio.wait_for(
                    bot.download_raw(cdn_media, aes_key),
                    timeout=dl_timeout
                )
                log.debug(f"[media] SDK CDN下载成功: {filename} ({len(data)}B)")
                break
            except asyncio.TimeoutError:
                log.warning(f"[media] SDK CDN下载超时({dl_timeout}s, attempt {attempt+1}): {filename}")
                data = None
            except Exception as e:
                log.warning(f"[media] SDK CDN下载失败(attempt {attempt+1}): {repr(e)}")
                data = None
                if attempt == 0:
                    await asyncio.sleep(1)   # 等一秒再重试

    # ── 方式2: media_item 本身就是 CDNMedia ──
    if data is None and hasattr(media_item, "encrypt_query_param") and bot:
        for attempt in range(2):
            try:
                data = await asyncio.wait_for(
                    bot.download_raw(media_item),
                    timeout=dl_timeout
                )
                log.debug(f"[media] SDK CDN下载(直接)成功: {filename}")
                break
            except asyncio.TimeoutError:
                log.warning(f"[media] SDK CDN下载(直接)超时({dl_timeout}s, attempt {attempt+1}): {filename}")
                data = None
            except Exception as e:
                log.warning(f"[media] SDK CDN下载(直接)失败(attempt {attempt+1}): {repr(e)}")
                data = None
                if attempt == 0:
                    await asyncio.sleep(1)

    # ── 方式3: 裸 HTTP 兜底（仅用于公开 URL，微信 CDN 大概率失败）──
    if data is None and fallback_url:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    fallback_url,
                    timeout=aiohttp.ClientTimeout(total=dl_timeout),
                    headers={"User-Agent": "Mozilla/5.0"}
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        # 尝试 AES 解密
                        if aes_key:
                            raw = _try_aes_decrypt(raw, aes_key)
                        data = raw
                        log.debug(f"[media] HTTP兜底下载成功: {filename}")
                    else:
                        log.warning(f"[media] HTTP兜底失败 {resp.status}: {fallback_url[:80]}")
        except asyncio.TimeoutError:
            log.warning(f"[media] HTTP兜底超时({dl_timeout}s): {filename}")
        except Exception as e:
            log.warning(f"[media] HTTP兜底异常: {e}")

    if data is None:
        log.error(f"[media] 所有下载方式均失败: {filename}")
        return None

    # ── 保存文件 ──
    try:
        cfg = _load_cfg()
        ws_upload = Path(cfg.get("paths", {}).get(
            "workspace_base", "/tmp/litecode_workspace")) / "uploads"
        ws_upload.mkdir(parents=True, exist_ok=True)
        local_path = ws_upload / filename
        local_path.write_bytes(data)
        # 备份到 media 目录 (每次都 mkdir, 防止目录被清理或 volume mount 点异常)
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        backup = _MEDIA_DIR / filename
        try:
            backup.write_bytes(data)
        except OSError as _be:
            log.warning(f"[media] backup 写入失败 (不致命): {_be}")
        log.info(f"[media] 已保存: {local_path} ({len(data)//1024}KB)")
        return str(local_path)
    except Exception as e:
        log.error(f"[media] 保存文件失败: {e}")
        return None


# ── 媒体下载 ─────────────────────────────────────────────────
_MEDIA_DIR = Path.home() / ".litecode" / "wechat_media"
_MEDIA_DIR.mkdir(parents=True, exist_ok=True)


async def _download_media(url: str, aes_key: str = "", ext: str = "bin",
                          filename: str = "", bot_id: str = "") -> Optional[str]:
    """下载微信媒体到 workspace/uploads，返回本地路径。"""
    if not url:
        return None
    try:
        if not filename:
            filename = f"wx_{bot_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}.{ext}"
        else:
            filename = re.sub(r'[^\w.\-]', '_', filename)

        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    log.warning(f"[media] 下载失败 HTTP {resp.status}: {url[:80]}")
                    return None
                data = await resp.read()

        # AES 解密（微信 CDN 媒体可能加密）
        if aes_key:
            data = _try_aes_decrypt(data, aes_key)

        # 保存到 workspace/uploads（agent 可访问）
        cfg = _load_cfg()
        ws_upload = Path(cfg.get("paths", {}).get(
            "workspace_base", "/tmp/litecode_workspace")) / "uploads"
        ws_upload.mkdir(parents=True, exist_ok=True)
        local_path = ws_upload / filename
        local_path.write_bytes(data)

        # 同时保存到 media 备份目录 (每次都 mkdir, 防目录丢)
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        backup = _MEDIA_DIR / filename
        try:
            backup.write_bytes(data)
        except OSError as _be:
            log.warning(f"[media] backup 写入失败 (不致命): {_be}")

        log.info(f"[media] 已保存: {local_path} ({len(data)} bytes)")
        return str(local_path)

    except Exception as e:
        log.error(f"[media] 下载异常: {e}")
        return None


def _try_aes_decrypt(data: bytes, aes_key: str) -> bytes:
    """尝试 AES-128-ECB 解密，失败返回原数据。"""
    try:
        import base64
        raw = aes_key.strip()
        if len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw):
            key = bytes.fromhex(raw)
        else:
            decoded = base64.b64decode(raw)
            key = decoded[:16] if len(decoded) >= 16 else decoded.ljust(16, b"\x00")

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(key), modes.ECB())
            pt = cipher.decryptor().update(data) + cipher.decryptor().finalize()
        except ImportError:
            from Crypto.Cipher import AES
            pt = AES.new(key, AES.MODE_ECB).decrypt(data)

        pad_len = pt[-1]
        if 1 <= pad_len <= 16 and all(b == pad_len for b in pt[-pad_len:]):
            pt = pt[:-pad_len]
        return pt
    except Exception:
        return data


# ── 图片视觉描述 ─────────────────────────────────────────────
async def _describe_image(local_path: str) -> str:
    """用配置的视觉模型描述图片内容，未配置则返回空。"""
    try:
        import base64
        cfg = _load_cfg()
        models_list = cfg.get("models", [])

        # [FIX 2026-08-31] 原先硬性优先 wechat.vision_model —— 实测那台
        # (Qwen3.6-35B, chat template 有缺陷) 返回空, 而本函数失败时静默返回 "",
        # 于是这条描述链路坏了很久无人发现。
        # 现改为优先用**主模型**(若它支持视觉) —— 它正在服务对话, 状态最可信;
        # 主模型不支持视觉时才退回 wechat.vision_model, 再退回任意视觉模型。
        _cands = []
        _main = cfg.get("model", {}) or {}
        if _main.get("supports_vision") and _main.get("backend_url"):
            _cands.append(_main)
        vision_id = cfg.get("wechat", {}).get("vision_model", "")
        for m in models_list:
            if vision_id and m.get("id") == vision_id and m.get("supports_vision"):
                _cands.append(m)
        for m in models_list:
            if m.get("supports_vision") and m not in _cands:
                _cands.append(m)
        if not _cands:
            log.warning("[vision] 没有可用的视觉模型, 跳过描述")
            return ""
        vision_model = _cands[0]

        img_data = Path(local_path).read_bytes()
        b64 = base64.b64encode(img_data).decode()
        ext = Path(local_path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")

        _last_err = ""
        for vision_model in _cands[:3]:      # [FIX] 逐个候选试, 第一个空/报错就换下一个
          try:
            url = vision_model["backend_url"].rstrip("/")
            headers = {"Authorization": f"Bearer {vision_model.get('api_key', '')}",
                       "Content-Type": "application/json"}
            # [FIX 2026-08-31] 用展示 ID 直接发会 404 —— 上游认的是 served_model_name
            # (实测: qwen3.8-flash-next-local → "The model does not exist",
            #  上游实际叫 Qwen3.8-Flash-Next)。这正是 wire_model_name 解耦要解决的,
            # 但本函数自己拼 payload 绕过了它。
            _wire = vision_model.get("served_model_name") or vision_model["id"]
            payload = {
            "model": _wire,
            "max_tokens": 500,
            "stream": False,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": (
                    "请仔细观察并用中文描述这张图片的内容（200字内）。\n"
                    "要求：\n"
                    "1. 先判断图片类型（如：K线图/股票行情、聊天截图、游戏截图、照片、表格、文档、图表等）\n"
                    "2. 如果是K线图/股票行情图，请识别：股票名称/代码、时间周期、大致走势（涨/跌/震荡）、关键价格\n"
                    "3. 如果是图表/数据图，请识别：图表类型、数据含义、关键数值\n"
                    "4. 如果有文字，请尽量识别关键文字内容\n"
                    "5. 不要猜测，只描述你确实看到的内容"
                )}
            ]}]
        }

            async with aiohttp.ClientSession() as sess:
                async with sess.post(f"{url}/chat/completions", headers=headers,
                                     json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200:
                        _last_err = f"{vision_model['id']} HTTP {resp.status}: {(await resp.text())[:120]}"
                        log.warning(f"[vision] {_last_err}")
                        continue
                    data = await resp.json()
                    desc = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                    # 清理 Qwen thinking 标签
                    if "<think>" in desc:
                        desc = re.sub(r"<think>.*?</think>", "", desc, flags=re.DOTALL).strip()
                    if desc:
                        log.info(f"[vision] 图片描述 ({vision_model['id']}, {len(desc)}字): {desc[:60]}")
                        return desc
                    _last_err = f"{vision_model['id']} 返回空内容"
                    log.warning(f"[vision] {_last_err}, 换下一个候选")
          except Exception as _ve:
            _last_err = f"{vision_model.get('id')}: {type(_ve).__name__}: {_ve}"
            log.warning(f"[vision] {_last_err}, 换下一个候选")
        # [FIX 2026-08-31] 原先失败走 log.debug 然后静默返回 "" —— 这条链路
        # 坏了很久无人发现 (实测 Qwen3.6-35B 恒返回空)。现在失败必须可见。
        log.error(f"[vision] 所有候选视觉模型都失败, 最后错误: {_last_err}")
    except Exception as e:
        log.error(f"[vision] 描述异常: {e}")
    return ""


# ── PDF 文本提取 ──────────────────────────────────────────────
def _extract_pdf_text(local_path: str, max_chars: int = 3000) -> str:
    """提取 PDF 前几页文本。"""
    # 尝试 pdfplumber（精确表格提取）
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(local_path) as pdf:
            for i, page in enumerate(pdf.pages[:10]):
                t = page.extract_text() or ""
                text_parts.append(t)
                if sum(len(p) for p in text_parts) > max_chars:
                    break
        result = "\n\n".join(text_parts).strip()
        if result:
            return result[:max_chars]
    except Exception:
        pass

    # 尝试 PyMuPDF
    try:
        import fitz
        doc = fitz.open(local_path)
        text_parts = []
        for i in range(min(10, len(doc))):
            text_parts.append(doc[i].get_text())
            if sum(len(p) for p in text_parts) > max_chars:
                break
        doc.close()
        result = "\n\n".join(text_parts).strip()
        if result:
            return result[:max_chars]
    except Exception:
        pass

    return ""


# ── Word 文档文本提取 ─────────────────────────────────────────
def _extract_docx_text(local_path: str, max_chars: int = 3000) -> str:
    """提取 .docx 文件的文本内容。"""
    try:
        from docx import Document
        doc = Document(local_path)
        paragraphs = []
        total = 0
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                paragraphs.append(t)
                total += len(t)
                if total > max_chars:
                    break
        # 也提取表格内容
        for table in doc.tables:
            if total > max_chars:
                break
            rows = []
            for row in table.rows[:20]:  # 最多20行
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(" | ".join(cells))
                total += len(rows[-1])
            if rows:
                paragraphs.append("[表格]\n" + "\n".join(rows))
        result = "\n".join(paragraphs).strip()
        return result[:max_chars] if result else ""
    except Exception as e:
        log.warning(f"[extract] docx 提取失败: {e}")
        return ""


# ── Excel / CSV 预览提取 ──────────────────────────────────────
def _extract_xlsx_preview(local_path: str, max_chars: int = 3000) -> str:
    """提取 .xlsx / .csv / .tsv 文件的前若干行预览。"""
    ext = Path(local_path).suffix.lower()
    try:
        import pandas as pd
        if ext == ".csv":
            df = pd.read_csv(local_path, nrows=30, encoding="utf-8", on_bad_lines="skip")
        elif ext == ".tsv":
            df = pd.read_csv(local_path, sep="\t", nrows=30, encoding="utf-8", on_bad_lines="skip")
        else:
            df = pd.read_excel(local_path, nrows=30, engine="openpyxl")

        info_parts = [
            f"形状: {df.shape[0]}行 × {df.shape[1]}列",
            f"列名: {', '.join(str(c) for c in df.columns[:20])}",
        ]
        # 数值列统计
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        if num_cols:
            info_parts.append(f"数值列: {', '.join(num_cols[:10])}")

        # 前5行预览
        preview = df.head(5).to_string(index=False, max_colwidth=30)
        info_parts.append(f"前5行预览:\n{preview}")

        result = "\n".join(info_parts)
        return result[:max_chars]
    except Exception as e:
        log.warning(f"[extract] xlsx/csv 提取失败: {e}")
        return ""


# ── PPT 文本提取 ──────────────────────────────────────────────
def _extract_pptx_text(local_path: str, max_chars: int = 3000) -> str:
    """提取 .pptx 文件中每页的文本内容。"""
    try:
        from pptx import Presentation
        prs = Presentation(local_path)
        parts = []
        total = 0
        for i, slide in enumerate(prs.slides):
            if total > max_chars:
                break
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t:
                            texts.append(t)
            if texts:
                slide_text = f"[第{i+1}页] " + " / ".join(texts)
                parts.append(slide_text)
                total += len(slide_text)
        result = "\n".join(parts).strip()
        return result[:max_chars] if result else ""
    except Exception as e:
        log.warning(f"[extract] pptx 提取失败: {e}")
        return ""


# ── 纯文本/代码文件预览 ──────────────────────────────────────
def _extract_text_preview(local_path: str, max_chars: int = 2000) -> str:
    """读取文本/代码文件的前若干行。"""
    try:
        with open(local_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 200)
        lines = content.splitlines()
        total_lines = len(lines)
        preview = "\n".join(lines[:60])  # 前60行
        if total_lines > 60:
            preview += f"\n... (共 {total_lines} 行)"
        return preview[:max_chars]
    except Exception as e:
        log.warning(f"[extract] text 预览失败: {e}")
        return ""


# ── 音频时长提取 ──────────────────────────────────────────────
def _extract_archive_overview(local_path: str, max_chars: int = 2500) -> str:
    """[v1.4] 复杂压缩包概览:
      - 文件数 + 总大小
      - 按扩展名聚合 (code/doc/image/...)
      - 前 30 条文件清单 + 大小
      - 顶层 README / 配置文件文本预览
    zip/tar/gz/tgz/bz2/xz/7z/rar 都支持.
    """
    import subprocess as _sp
    p = Path(local_path)
    ext = p.suffix.lower()
    size = p.stat().st_size if p.exists() else 0
    size_str = f"{size // 1024}KB" if size < 1048576 else f"{size // 1048576}MB"
    header = f"路径: {local_path}\n文件名: {p.name} ({size_str})"

    # 取清单: "<size>\t<path>" 格式
    entries: list = []
    try:
        if ext == ".zip":
            r = _sp.run(["unzip", "-l", "-qq", local_path],
                        capture_output=True, text=True, timeout=15)
            for line in r.stdout.splitlines():
                m = re.match(r"^\s*(\d+)\s+\S+\s+\S+\s+(.+)$", line)
                if m:
                    entries.append((int(m.group(1)), m.group(2).strip()))
        elif ext in (".tar", ".gz", ".tgz", ".bz2", ".xz"):
            r = _sp.run(["tar", "tvf", local_path],
                        capture_output=True, text=True, timeout=15)
            for line in r.stdout.splitlines():
                # -rw-r--r-- user/group  SIZE DATE TIME PATH
                m = re.match(r"^\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+(.+)$", line)
                if m:
                    entries.append((int(m.group(1)), m.group(2).strip()))
        elif ext == ".7z":
            r = _sp.run(["7z", "l", local_path],
                        capture_output=True, text=True, timeout=15)
            in_table = False
            for line in r.stdout.splitlines():
                if line.startswith("---"):
                    in_table = not in_table
                    continue
                if in_table:
                    parts = line.rsplit(None, 4)
                    if len(parts) >= 5 and parts[-1] and parts[-3].isdigit():
                        entries.append((int(parts[-3]), parts[-1]))
        elif ext == ".rar":
            r = _sp.run(["unrar", "l", "-cfg-", local_path],
                        capture_output=True, text=True, timeout=15)
            for line in r.stdout.splitlines():
                m = re.match(r"^\s*[\-\.A-Z]+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$", line)
                if m:
                    entries.append((int(m.group(1)), m.group(2).strip()))
    except Exception as e:
        log.warning(f"[archive] 列表失败 {local_path}: {e}")

    if not entries:
        return (f"{header}\n[压缩包]: 列表失败 (工具可能未安装 / 格式不支持)\n"
                f"请用 execute_shell 解压查看")

    # 聚合统计
    from collections import Counter as _Counter
    _KIND = {
        "code": {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".c",
                 ".cpp", ".h", ".rb", ".php", ".lua", ".sh", ".swift"},
        "doc":  {".md", ".txt", ".pdf", ".doc", ".docx", ".rtf"},
        "sheet":{".xls", ".xlsx", ".csv", ".tsv"},
        "slide":{".ppt", ".pptx"},
        "image":{".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"},
        "video":{".mp4", ".avi", ".mov", ".mkv", ".webm"},
        "audio":{".mp3", ".wav", ".ogg", ".flac", ".m4a"},
        "data": {".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg"},
        "archive":{".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"},
    }
    kind_count: dict = _Counter()
    for _, name in entries:
        e = Path(name).suffix.lower()
        found = False
        for k, exts in _KIND.items():
            if e in exts:
                kind_count[k] += 1
                found = True
                break
        if not found:
            kind_count["other"] += 1

    total_size = sum(sz for sz, _ in entries)
    total_size_str = (f"{total_size // 1024}KB" if total_size < 1048576
                      else f"{total_size // 1048576}MB")

    # Top 级 README / package.json / *.md 文本预览 (仅 zip 能快速 unzip -p)
    readme_preview = ""
    if ext == ".zip":
        preview_targets = [n for _, n in entries
                           if Path(n).name.lower() in
                              ("readme.md", "readme.txt", "readme",
                               "package.json", "setup.py", "pyproject.toml",
                               "cargo.toml", "go.mod")][:3]
        for t in preview_targets:
            try:
                r = _sp.run(["unzip", "-p", local_path, t],
                            capture_output=True, text=True, timeout=8)
                if r.returncode == 0 and r.stdout.strip():
                    snippet = r.stdout[:600]
                    readme_preview += f"\n--- {t} ---\n{snippet}\n"
            except Exception:
                pass

    lines = [
        header,
        f"[压缩包概览] 格式={ext[1:]}, 条目={len(entries)}, 内部总大小={total_size_str}",
        f"[类型分布] " + ", ".join(f"{k}={v}" for k, v in
                                   sorted(kind_count.items(),
                                          key=lambda x: -x[1])),
        f"[文件清单 前{min(30, len(entries))}/{len(entries)}]:",
    ]
    for sz, name in entries[:30]:
        sz_s = f"{sz // 1024}K" if sz >= 1024 else f"{sz}B"
        lines.append(f"  {sz_s:>8s}  {name}")
    if len(entries) > 30:
        lines.append(f"  ... 省略 {len(entries) - 30} 条 (用 execute_shell 看完整)")
    if readme_preview:
        lines.append("[顶层文件预览]:" + readme_preview)
    out = "\n".join(lines)
    if len(out) > max_chars + 500:
        out = out[:max_chars] + "\n... (清单截断)"
    return out


def _extract_audio_info(local_path: str) -> str:
    """用 ffprobe 获取音频/视频基本信息。"""
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", local_path],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            import json
            info = json.loads(r.stdout)
            fmt = info.get("format", {})
            duration = float(fmt.get("duration", 0))
            size = int(fmt.get("size", 0))
            codec = ""
            for s in info.get("streams", []):
                if s.get("codec_type") == "audio":
                    codec = s.get("codec_name", "")
                    break
                if s.get("codec_type") == "video":
                    w = s.get("width", "?")
                    h = s.get("height", "?")
                    codec = f"{s.get('codec_name','')} {w}x{h}"
                    break
            parts = []
            if duration:
                m, s_ = divmod(int(duration), 60)
                parts.append(f"时长: {m}分{s_}秒")
            if size:
                parts.append(f"大小: {size // 1024}KB")
            if codec:
                parts.append(f"编码: {codec}")
            return ", ".join(parts) if parts else ""
    except Exception as e:
        log.warning(f"[extract] 音频信息失败: {e}")
    return ""

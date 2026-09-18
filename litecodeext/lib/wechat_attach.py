"""lib/wechat_attach.py — 微信桥附件预处理 + 视觉家族（从 wechat_bridge.py 抽取, P2-7）
语音 Whisper 兜底转写 / 主模型视觉判定 / 图片转 data URL / 扩展名分类 / 附件预处理调度器。
纯搬运，逻辑未变。
"""
import json
import logging
from pathlib import Path
from typing import Optional

from lib.wechat_media import (
    _describe_image, _extract_pdf_text, _extract_docx_text, _extract_xlsx_preview,
    _extract_pptx_text, _extract_text_preview, _extract_archive_overview,
    _extract_audio_info,
)

log = logging.getLogger("wechat_bridge")

_BASE = Path(__file__).parent.parent


def _load_cfg() -> dict:
    p = _BASE / "config.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


# ── 语音识别（Whisper 本地降级）────────────────────────────
def _try_whisper_transcribe(voice_path: str = "", voice_url: str = "") -> str:
    """
    尝试用 Whisper 识别语音内容：
    1. 如果有本地文件路径，直接识别
    2. 如果有 URL，先下载再识别
    3. 使用 openai-whisper CLI 或 faster-whisper
    返回识别文本，失败返回空字符串。
    """
    import subprocess
    import tempfile

    target_file = ""
    tmp_files = []

    try:
        if voice_path and Path(voice_path).exists():
            target_file = voice_path
        elif voice_url:
            # 下载语音文件
            import httpx as _hx
            resp = _hx.get(voice_url, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                tmp = tempfile.NamedTemporaryFile(suffix=".amr", delete=False)
                tmp.write(resp.content)
                tmp.close()
                target_file = tmp.name
                tmp_files.append(tmp.name)

        if not target_file:
            return ""

        # 转换为 wav（silk/amr → wav）
        wav_file = target_file + ".wav"
        tmp_files.append(wav_file)
        convert_result = subprocess.run(
            ["ffmpeg", "-y", "-i", target_file, "-ar", "16000", "-ac", "1", wav_file],
            capture_output=True, timeout=10
        )
        if convert_result.returncode != 0:
            # 可能是 silk 格式，尝试直接用
            wav_file = target_file

        # 尝试 whisper CLI
        for cmd in ["whisper", "faster-whisper"]:
            try:
                result = subprocess.run(
                    [cmd, wav_file, "--language", "zh", "--model", "base",
                     "--output_format", "txt", "--output_dir", "/tmp"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    # 读取输出文件
                    txt_file = Path("/tmp") / (Path(wav_file).stem + ".txt")
                    if txt_file.exists():
                        text = txt_file.read_text().strip()
                        txt_file.unlink(missing_ok=True)
                        if text:
                            log.info(f"[whisper] 识别成功: {text[:60]}")
                            return text
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        # 尝试通过 API 调用（如果配置了 Whisper 兼容的端点）
        cfg = _load_cfg()
        whisper_url = cfg.get("whisper", {}).get("api_url", "")
        if whisper_url:
            try:
                import httpx as _hx
                with open(wav_file, "rb") as f:
                    resp = _hx.post(
                        whisper_url,
                        files={"file": ("audio.wav", f, "audio/wav")},
                        data={"model": "whisper-1", "language": "zh"},
                        timeout=30
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("text", "").strip()
                    if text:
                        return text
            except Exception as e:
                log.warning(f"[whisper] API 调用失败: {e}")

    except Exception as e:
        log.warning(f"[whisper] 识别失败: {e}")
    finally:
        # 清理临时文件
        for f in tmp_files:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass

    return ""


def _main_model_supports_vision() -> bool:
    """[v1.4] 当前活跃主模型是否支持视觉."""
    try:
        cfg = _load_cfg()
        return bool(cfg.get("model", {}).get("supports_vision", False))
    except Exception:
        return False


def _image_to_data_url(local_path: str, max_side: int = 1280,
                       quality: int = 82,
                       raw_threshold_bytes: int = 2 * 1024 * 1024) -> Optional[str]:
    """[v1.4] 读图生成 OpenAI 格式 data URL, 给多模态主模型直接吃.
    - ≤2MB 原样 base64
    - >2MB PIL 下采样到长边 1280 + JPEG q=82
    - 兼容 Pillow 10+ (Image.Resampling.LANCZOS)"""
    try:
        import base64, io
        p = Path(local_path)
        if not p.exists():
            log.warning(f"[vision] 图片不存在: {local_path}")
            return None
        ext = p.suffix.lower().lstrip(".")
        raw = p.read_bytes()
        size_kb = len(raw) // 1024
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp",
                "bmp": "image/bmp"}.get(ext, "image/jpeg")
        if len(raw) <= raw_threshold_bytes:
            b64 = base64.b64encode(raw).decode()
            log.info(f"[vision] {p.name} 原样直传 ({size_kb}KB)")
            return f"data:{mime};base64,{b64}"
        try:
            from PIL import Image
            try:
                RESAMPLE = Image.Resampling.LANCZOS
            except AttributeError:
                RESAMPLE = getattr(Image, "LANCZOS", 1)
            img = Image.open(io.BytesIO(raw))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            scale = max_side / max(w, h)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), RESAMPLE)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            compressed = out.getvalue()
            b64 = base64.b64encode(compressed).decode()
            log.info(f"[vision] {p.name} 压缩 {size_kb}KB → {len(compressed)//1024}KB")
            return f"data:image/jpeg;base64,{b64}"
        except ImportError:
            log.warning(f"[vision] PIL 未安装, {p.name} 原样传 ({size_kb}KB)")
            b64 = base64.b64encode(raw).decode()
            return f"data:{mime};base64,{b64}"
    except Exception as e:
        log.warning(f"[vision] data URL 生成失败 {local_path}: {e}")
        return None


# 文件扩展名 → 处理类别映射
_EXT_CATEGORY = {
    # 图片 → 视觉模型识别
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".gif": "image", ".webp": "image", ".bmp": "image",
    # 文档
    ".pdf": "pdf",
    ".doc": "docx", ".docx": "docx",
    ".xls": "xlsx", ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".csv": "csv", ".tsv": "csv",
    ".ppt": "pptx", ".pptx": "pptx",
    # 代码 / 纯文本
    ".py": "text", ".js": "text", ".ts": "text", ".jsx": "text",
    ".tsx": "text", ".go": "text", ".rs": "text", ".java": "text",
    ".c": "text", ".cpp": "text", ".h": "text", ".hpp": "text",
    ".rb": "text", ".php": "text", ".swift": "text", ".kt": "text",
    ".sh": "text", ".bash": "text", ".zsh": "text",
    ".html": "text", ".css": "text", ".scss": "text",
    ".json": "text", ".yaml": "text", ".yml": "text",
    ".toml": "text", ".ini": "text", ".cfg": "text", ".conf": "text",
    ".xml": "text", ".sql": "text", ".r": "text",
    ".txt": "text", ".md": "text", ".rst": "text", ".log": "text",
    # 音视频
    ".mp3": "audio", ".wav": "audio", ".flac": "audio",
    ".aac": "audio", ".ogg": "audio", ".m4a": "audio",
    ".silk": "audio", ".amr": "audio",
    ".mp4": "video", ".avi": "video", ".mov": "video",
    ".mkv": "video", ".webm": "video", ".flv": "video",
    # 压缩包
    ".zip": "archive", ".rar": "archive", ".7z": "archive",
    ".tar": "archive", ".gz": "archive", ".bz2": "archive",
    ".tgz": "archive", ".xz": "archive",
}


async def _preprocess_attachment(local_path: str, max_chars: int = 0) -> str:
    """
    根据文件类型自动预处理附件，返回人类可读的内容摘要。
    这样主模型不需要 vision 能力也能理解附件内容。

    max_chars: 单个附件的内容摘要字符上限，0 表示使用默认值。
    返回格式: "路径: xxx\n[类型标签]: 内容摘要"
    """
    if max_chars <= 0:
        cfg = _load_cfg()
        max_chars = cfg.get("wechat", {}).get("attachment_preview_chars", 2000)

    p = Path(local_path)
    ext = p.suffix.lower()
    fname = p.name
    category = _EXT_CATEGORY.get(ext, "unknown")

    # 文件基本信息
    try:
        size = p.stat().st_size
        size_str = f"{size // 1024}KB" if size < 1048576 else f"{size // 1048576}MB"
    except Exception:
        size_str = "未知大小"

    header = f"路径: {local_path}\n文件名: {fname} ({size_str})"

    try:
        if category == "image":
            # [v1.4] 主模型支持视觉 → 图片会以 data URL 嵌入 user content,
            # 这里只放占位符并强提示, 阻止 agent 继续跑 OCR/PIL.
            if _main_model_supports_vision():
                # [FIX 2026-08-31] 原文案声称"你已经能直接看到像素"并禁用一切工具。
                # 这句话**只在图片到达的那一轮为真** —— 落盘时只存文本, 像素不入库,
                # 于是任何重试/追问/上下文裁剪之后, 它就变成一句无法执行的假指令:
                # 模型看不到图, 又被禁止用工具去看。实测该会话连续两次卡死在此。
                # 现在说实话, 并给出唯一正确的补救路径。
                # [FIX 2026-08-31] 到达即描述并落库。
                # 图片到达的这一刻是唯一"免费"看到它的时机 —— 主模型本轮就带着像素。
                # 但像素不入库 (会话里多模态消息数恒为 0), 一旦中断重试/追问/裁剪,
                # 后续轮次就只剩一句"你已经能看到像素"的假话, 模型被锁死。
                # 现在同轮产出一段文字描述存进历史: 约 250 t 永久保留,
                # 而重新塞回 base64 是每张每轮 1600 t。描述有损, 原图仍在盘上,
                # 需要细节时模型自己调 vision_ocr 回读。
                _desc = ""
                try:
                    _desc = await _describe_image(local_path)
                except Exception as _de:
                    log.warning(f"[vision] 图片描述失败 {fname}: {_de}")
                _desc_block = (f"[图片描述 (到达时生成, 永久保留)]: {_desc[:max_chars]}\n"
                               if _desc else
                               "[图片描述]: 生成失败, 本条只能靠本轮像素或 vision_ocr 回读\n")
                return (f"{header}\n"
                        f"[file: {local_path}]\n"
                        f"{_desc_block}"
                        f"这张图**本轮**已作为多模态输入嵌入; 若你在上下文里确实看得到像素, "
                        f"以像素为准 (描述可能有损), 不要调用 read_file / execute_shell / "
                        f"pytesseract / PIL — 那些读的是二进制不是像素.\n"
                        f"若你**看不到**像素 (常见于: 上一轮被中断后重试、历史被裁剪、"
                        f"或本条来自历史记录), 就用上面的描述回答; 描述不够细时"
                        f"用 `vision_ocr({local_path})` 回读原图。"
                        f"不许假装看到, 也不许跳过这张图。")
            desc = await _describe_image(local_path)
            if desc:
                return f"{header}\n[图片内容识别]: {desc[:max_chars]}"
            else:
                return f"{header}\n[图片]: 视觉识别失败，请用工具查看"

        elif category == "pdf":
            text = _extract_pdf_text(local_path, max_chars=max_chars)
            if text:
                return f"{header}\n[PDF内容预览]:\n{text}"
            else:
                return f"{header}\n[PDF]: 文本提取失败（可能是扫描件），请用工具查看"

        elif category == "docx":
            text = _extract_docx_text(local_path, max_chars=max_chars)
            if text:
                return f"{header}\n[Word文档内容预览]:\n{text}"
            else:
                return f"{header}\n[Word文档]: 内容提取失败，请用工具查看"

        elif category in ("xlsx", "csv"):
            text = _extract_xlsx_preview(local_path, max_chars=max_chars)
            if text:
                label = "Excel表格" if category == "xlsx" else "CSV数据"
                return f"{header}\n[{label}预览]:\n{text}"
            else:
                return f"{header}\n[表格]: 内容提取失败，请用工具查看"

        elif category == "pptx":
            text = _extract_pptx_text(local_path, max_chars=max_chars)
            if text:
                return f"{header}\n[PPT内容预览]:\n{text}"
            else:
                return f"{header}\n[PPT]: 内容提取失败，请用工具查看"

        elif category == "text":
            text = _extract_text_preview(local_path, max_chars=min(max_chars, 2000))
            if text:
                return f"{header}\n[文件内容预览]:\n{text}"
            else:
                return f"{header}\n[文本文件]: 内容读取失败"

        elif category in ("audio", "video"):
            info = _extract_audio_info(local_path)
            label = "音频" if category == "audio" else "视频"
            if info:
                return f"{header}\n[{label}信息]: {info}"
            else:
                return f"{header}\n[{label}]: 已保存，可用工具处理"

        elif category == "archive":
            # [v1.4] 复杂压缩包: 结构化清单 + 文件类型聚合统计 + top 级内容
            # 预览, 让模型第一眼就能判断"里面是啥".
            return _extract_archive_overview(local_path, max_chars=max_chars)

        else:
            return f"{header}\n[未知类型 {ext}]: 已保存，可用工具查看"

    except Exception as e:
        log.warning(f"[preprocess] 附件预处理异常 {fname}: {e}")
        return f"{header}\n[预处理失败]: {e}"

#!/usr/bin/env python3
"""语音转文字 (ASR), 按可用性自动选后端:

  1. faster-whisper   本地已装且模型已缓存时 (不自动下载模型; 设 ASR_WHISPER_MODEL=small|模型目录 启用)
  2. dashscope        阿里百炼 qwen3-asr-flash (OpenAI 兼容接口), key 取 env DASHSCOPE_API_KEY,
                      没有就从 LiteCode config.json 里 backend_url 含 dashscope 的模型复用 api_key
  3. none             都没有 → 返回空, 调用方退化为关键帧 OCR 字幕

用法: python3 asr.py <音频或视频文件> [--backend auto|dashscope|whisper] [--json]
环境变量 LITECODE_ASR=none 可强制关闭 ASR。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

CHUNK_S = 170          # qwen3-asr-flash 单次上限约 3 分钟, 留余量
DS_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DS_MODEL = os.environ.get("ASR_DASHSCOPE_MODEL", "qwen3-asr-flash")


def _config_paths():
    here = Path(__file__).resolve()
    env = os.environ.get("LITECODE_CONFIG")
    if env:
        yield Path(env)
    for up in (here.parents[3], here.parents[4] if len(here.parents) > 4 else here.parents[3]):
        yield up / "config.json"
    yield Path("/opt/litecode/config.json")


def dashscope_key() -> str:
    if os.environ.get("DASHSCOPE_API_KEY"):
        return os.environ["DASHSCOPE_API_KEY"]
    for p in _config_paths():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        asr = cfg.get("asr") or {}
        if asr.get("dashscope_api_key"):
            return asr["dashscope_api_key"]
        for m in cfg.get("models") or []:
            if "dashscope" in str(m.get("backend_url", "")) and m.get("api_key"):
                return m["api_key"]
    return ""


def _whisper_model():
    name = os.environ.get("ASR_WHISPER_MODEL", "")
    if not name:
        return None
    try:
        from faster_whisper import WhisperModel  # noqa
    except Exception:
        return None
    try:  # local_files_only: 不偷偷下载几百 MB 模型
        return WhisperModel(name, device="auto", compute_type="int8", local_files_only=True)
    except Exception:
        return None


def available_backend() -> str:
    if os.environ.get("LITECODE_ASR", "").lower() == "none":
        return "none"
    if os.environ.get("ASR_WHISPER_MODEL"):
        try:
            import faster_whisper  # noqa
            return "whisper"
        except Exception:
            pass
    if dashscope_key():
        return "dashscope"
    return "none"


def _ffprobe_dur(p) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _to_chunks(src: Path, tmp: Path) -> list:
    """统一转 16k 单声道 mp3, 按 CHUNK_S 切段; 返回 [(start_s, path)]。"""
    dur = _ffprobe_dur(src)
    chunks, start, i = [], 0.0, 0
    while start < max(dur, 0.1):
        p = tmp / f"c{i:03d}.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-t", f"{CHUNK_S}", "-i", str(src),
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(p)], check=True)
        if p.exists() and p.stat().st_size > 1000:
            chunks.append((start, p))
        start += CHUNK_S
        i += 1
    return chunks


def _dashscope(chunks, key) -> list:
    segs = []
    for start, p in chunks:
        b64 = base64.b64encode(p.read_bytes()).decode()
        body = {"model": DS_MODEL, "stream": False,
                "messages": [{"role": "user", "content": [
                    {"type": "input_audio", "input_audio": {"data": "data:audio/mpeg;base64," + b64}}]}],
                "asr_options": {"enable_lid": True, "enable_itn": True}}
        r = requests.post(DS_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
        if r.status_code != 200:
            raise RuntimeError(f"dashscope ASR HTTP {r.status_code}: {r.text[:200]}")
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        segs.append({"start": round(start, 2), "end": round(start + _ffprobe_dur(p), 2), "text": text})
    return segs


def _whisper(src: Path) -> list:
    model = _whisper_model()
    if model is None:
        raise RuntimeError("faster-whisper 模型未缓存 (ASR_WHISPER_MODEL)")
    segments, _ = model.transcribe(str(src), language="zh", vad_filter=True)
    return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]


def transcribe(path: str, backend: str = "auto") -> dict:
    src = Path(path)
    be = available_backend() if backend == "auto" else backend
    if be == "none":
        return {"backend": "none", "text": "", "segments": [],
                "note": "没有可用 ASR (无 faster-whisper 模型, 也没有 DashScope key), 只能靠画面字幕 OCR"}
    try:
        if be == "whisper":
            segs = _whisper(src)
        else:
            with tempfile.TemporaryDirectory() as td:
                segs = _dashscope(_to_chunks(src, Path(td)), dashscope_key())
    except Exception as e:
        return {"backend": be, "text": "", "segments": [], "error": str(e)}
    text = "".join(s["text"] for s in segs).strip()
    return {"backend": be if be != "dashscope" else f"dashscope:{DS_MODEL}", "text": text, "segments": segs,
            "note": "" if text else "音频里没识别出人声 (可能是纯音乐/音效), 文案以画面字幕为准"}


def main():
    ap = argparse.ArgumentParser(description="语音转文字")
    ap.add_argument("path")
    ap.add_argument("--backend", default="auto", choices=["auto", "dashscope", "whisper", "none"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = transcribe(a.path, a.backend)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else (r["text"] or f"[{r['backend']}] {r.get('error') or r.get('note')}"))
    return 0 if r["text"] or r["backend"] == "none" else 1


if __name__ == "__main__":
    sys.exit(main())

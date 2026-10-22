"""core/tts_local.py — 本地纯 CPU 语音合成 (sherpa-onnx + Kokoro 多语言 v1.1, 中英混读, 不联网)。

模型目录 (kokoro-multi-lang-v1_1, 约 410MB) 按顺序找:
  环境变量 LITECODE_TTS_MODEL_DIR > config.tts.local_model_dir > WORKSPACE/models/kokoro-multi-lang-v1_1
  > /opt/litecode-models/kokoro-multi-lang-v1_1
没有 sherpa-onnx 或模型时 available() 为 False, tts.py 自动跳过本引擎。
2026-10-22 实测 (8 核 CPU, 4 线程): 加载 4.4s, 实时率约 0.8; 语音识别回读中文一字不差 (MeloTTS 把"下跌"读成"大跌", 没选)。
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

MODEL_NAME = "kokoro-multi-lang-v1_1"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL_NAME}.tar.bz2"

# 说话人编号 → (中文名, 性别, 说明)。0–2 是英文母语音色, 3–57 女声, 58 起男声 (按基频实测分出)。
VOICES: dict[int, tuple[str, str, str]] = {
    3: ("女声1", "女", "明亮, 默认"),
    26: ("女声2", "女", "温柔"),
    42: ("女声3", "女", "知性"),
    58: ("男声1", "男", "低沉稳重"),
    74: ("男声2", "男", "沉稳"),
    90: ("男声3", "男", "播报"),
}
DEFAULT_SID = 3
# 在线引擎的常用音色名 → 最接近的本地音色 (方言、粤语、台湾腔本地没有, 交给在线引擎)
ALIASES = {"女声": 3, "晓晓": 3, "芊悦": 3, "cherry": 3, "xiaoxiao": 3, "晓伊": 26, "苏瑶": 26, "xiaoyi": 26,
           "千雪": 26, "男声": 58, "云希": 58, "晨煦": 58, "yunxi": 58, "云健": 74, "yunjian": 74,
           "云扬": 90, "yunyang": 90, "墨讲师": 90}
# 品牌/英文缩写读音 (模型对这些读不准)
READINGS = {"LiteCode": "莱特扣得", "litecode": "莱特扣得", "ComfyUI": "康飞 U I", "API": "A P I"}

_lock = threading.Lock()
_tts = None


def model_dir() -> Path | None:
    cands = [os.environ.get("LITECODE_TTS_MODEL_DIR", "")]
    try:
        from lib.config import load_cfg, WORKSPACE
        cands.append(str((load_cfg().get("tts") or {}).get("local_model_dir") or ""))
        cands.append(str(Path(WORKSPACE) / "models" / MODEL_NAME))
    except Exception:
        pass
    cands.append(f"/opt/litecode-models/{MODEL_NAME}")
    for c in cands:
        if c and (Path(c) / "model.onnx").is_file() and (Path(c) / "voices.bin").is_file():
            return Path(c)
    return None


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return model_dir() is not None


def resolve(voice: str | None) -> tuple[int | None, str]:
    """→ (说话人编号, 提示)。识别不了返回 (None, 原因), 由调用方换引擎。"""
    v = str(voice or "").strip()
    if not v:
        return DEFAULT_SID, ""
    low = v.lower()
    for sid, (cn, _, _) in VOICES.items():
        if low == cn.lower():
            return sid, ""
    if low in ALIASES:
        return ALIASES[low], ""
    m = re.fullmatch(r"(?:local[:#]?|sid[:#]?)?(\d{1,3})", low)
    if m and int(m.group(1)) < 103:
        return int(m.group(1)), ""
    return None, f"本地引擎没有音色 {v}"


def _load():
    global _tts
    with _lock:
        if _tts is None:
            import sherpa_onnx as so
            d = model_dir()
            if d is None:
                raise RuntimeError("本地语音模型未安装")
            threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            _tts = so.OfflineTts(so.OfflineTtsConfig(
                model=so.OfflineTtsModelConfig(
                    kokoro=so.OfflineTtsKokoroModelConfig(
                        model=str(d / "model.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
                        data_dir=str(d / "espeak-ng-data"), dict_dir=str(d / "dict"),
                        lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}"),
                    num_threads=threads, provider="cpu"),
                rule_fsts=",".join(str(d / f) for f in ("date-zh.fst", "phone-zh.fst", "number-zh.fst")),
            ))
    return _tts


def _speed(rate) -> float:
    """'+20%' / '-10%' / 1.2 → 语速倍数 (0.5–2.0)。"""
    s = str(rate or "").strip().replace("%", "")
    if not s:
        return 1.0
    try:
        x = float(s)
    except ValueError:
        return 1.0
    sp = x if ("." in s and 0 < x <= 3 and not s.startswith(("+", "-"))) else 1 + x / 100
    return max(0.5, min(2.0, sp))


def synth_wav(text: str, sid: int, out: Path, rate=None) -> None:
    """同步合成一段到 wav (调用方放线程里跑)。同一模型实例串行使用。"""
    import soundfile as sf
    for k, v in READINGS.items():
        text = text.replace(k, v)
    tts = _load()
    with _lock:
        audio = tts.generate(text, sid=sid, speed=_speed(rate))
    if not len(audio.samples):
        raise RuntimeError("本地引擎没有生成音频 (文本可能只有符号)")
    sf.write(str(out), audio.samples, audio.sample_rate)

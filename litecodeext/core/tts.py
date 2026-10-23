"""core/tts.py — 文字转语音 (text_to_speech 工具的实现)。

引擎 (可插拔, 按 config.tts.engines 顺序自动选第一个可用的, 默认 local → edge → dashscope; 某引擎失败自动换下一个):
  local   本地纯 CPU (sherpa-onnx + Kokoro, core/tts_local.py), 不联网不花钱; 没装模型时自动跳过。
  edge    微软在线神经语音 (edge-tts, 免 key, 镜像里已装)。
  dashscope  阿里百炼 qwen3-tts-flash (有百炼 key 才可用; 音色更自然, 带方言)。
           key 来源: 环境变量 DASHSCOPE_API_KEY > config.tts.dashscope_api_key > config.asr.dashscope_api_key
           > config.models[] 里 backend_url 含 dashscope 的条目。
  auto    明确是某引擎独有的音色 (百炼的方言音色、edge 的粤语/台湾腔/英文音色) 就优先用那个引擎, 否则按顺序。

长文本按句切成 ≤300 字的段逐段合成, 再用 ffmpeg 拼接并统一转码成目标格式。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import httpx
from pathlib import Path

MAX_TEXT_CHARS = 10000
CHUNK_CHARS = 300
FORMATS = ("mp3", "wav", "m4a")
DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_DS_VOICE = "Cherry"
DS_MODEL = os.environ.get("TTS_DASHSCOPE_MODEL", "qwen3-tts-flash")
DS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

# edge-tts 免费可用的音色 (2026-10 `edge-tts --list-voices` 实测; 晓辰/晓涵/晓萱等 Azure 付费音色不提供)
EDGE_VOICES: dict[str, tuple[str, str, str]] = {   # id → (中文名, 性别, 风格)
    "zh-CN-XiaoxiaoNeural": ("晓晓", "女", "温暖知性, 通用首选"),
    "zh-CN-XiaoyiNeural": ("晓伊", "女", "活泼俏皮"),
    "zh-CN-YunxiNeural": ("云希", "男", "阳光讲故事"),
    "zh-CN-YunjianNeural": ("云健", "男", "沉稳有力, 体育/纪录片"),
    "zh-CN-YunyangNeural": ("云扬", "男", "新闻播音"),
    "zh-CN-YunxiaNeural": ("云夏", "男", "少年音"),
    "zh-CN-liaoning-XiaobeiNeural": ("晓北", "女", "东北话"),
    "zh-CN-shaanxi-XiaoniNeural": ("晓妮", "女", "陕西话"),
    "zh-HK-HiuGaaiNeural": ("晓佳", "女", "粤语"),
    "zh-HK-HiuMaanNeural": ("晓曼", "女", "粤语, 柔和"),
    "zh-HK-WanLungNeural": ("云龙", "男", "粤语"),
    "zh-TW-HsiaoChenNeural": ("晓臻", "女", "台湾腔, 亲切"),
    "zh-TW-HsiaoYuNeural": ("晓雨", "女", "台湾腔, 轻柔"),
    "zh-TW-YunJheNeural": ("云哲", "男", "台湾腔"),
    "en-US-JennyNeural": ("Jenny", "女", "美式英语"),
    "en-US-AriaNeural": ("Aria", "女", "美式英语"),
    "en-US-EmmaMultilingualNeural": ("Emma", "女", "美式英语, 多语种"),
    "en-US-GuyNeural": ("Guy", "男", "美式英语"),
    "en-US-AndrewMultilingualNeural": ("Andrew", "男", "美式英语, 多语种"),
    "en-US-BrianNeural": ("Brian", "男", "美式英语"),
    "en-GB-SoniaNeural": ("Sonia", "女", "英式英语"),
    "en-GB-RyanNeural": ("RyanUK", "男", "英式英语"),
}
# 百炼 qwen3-tts-flash 音色 (2026-10 实测均可用)
DS_VOICES: dict[str, tuple[str, str, str]] = {
    "Cherry": ("芊悦", "女", "阳光亲切, 默认"),
    "Serena": ("苏瑶", "女", "温柔"),
    "Chelsie": ("千雪", "女", "二次元少女"),
    "Ethan": ("晨煦", "男", "阳光温暖"),
    "Nofish": ("不吃鱼", "男", "不卷舌的设计师"),
    "Jennifer": ("詹妮弗", "女", "美式英语"),
    "Ryan": ("甜茶", "男", "节奏感强"),
    "Katerina": ("卡捷琳娜", "女", "御姐"),
    "Elias": ("墨讲师", "男", "讲解"),
    "Dylan": ("北京-晓东", "男", "北京话"),
    "Jada": ("上海-阿珍", "女", "上海话"),
    "Sunny": ("四川-晴儿", "女", "四川话"),
    "Eric": ("四川-程川", "男", "四川话"),
    "Li": ("南京-老李", "男", "南京话"),
    "Marcus": ("陕西-秦川", "男", "陕西话"),
    "Roy": ("闽南-阿杰", "男", "闽南语"),
    "Peter": ("天津-李彼得", "男", "天津话"),
    "Rocky": ("粤语-阿强", "男", "粤语"),
    "Kiki": ("粤语-阿清", "女", "粤语"),
}
# 常被叫到但 edge-tts 不提供的名字 → 最接近的免费音色
EDGE_FALLBACK = {"晓萱": "zh-CN-XiaoxiaoNeural", "晓辰": "zh-CN-XiaoxiaoNeural", "晓涵": "zh-CN-XiaoxiaoNeural",
                 "晓墨": "zh-CN-XiaoyiNeural", "晓秋": "zh-CN-XiaoxiaoNeural", "云枫": "zh-CN-YunxiNeural",
                 "云皓": "zh-CN-YunyangNeural", "云野": "zh-CN-YunjianNeural"}
_EXTRA_ALIASES = {"粤语": "zh-HK-HiuGaaiNeural", "台湾": "zh-TW-HsiaoChenNeural", "东北话": "zh-CN-liaoning-XiaobeiNeural",
                  "陕西话": "zh-CN-shaanxi-XiaoniNeural", "hiugaai": "zh-HK-HiuGaaiNeural",
                  "hsiaochen": "zh-TW-HsiaoChenNeural", "ryan uk": "en-GB-RyanNeural", "emma": "en-US-EmmaMultilingualNeural",
                  "andrew": "en-US-AndrewMultilingualNeural", "男声": "zh-CN-YunxiNeural", "女声": "zh-CN-XiaoxiaoNeural"}


def _edge_aliases() -> dict:
    out = {}
    for vid, (cn, _, _) in EDGE_VOICES.items():
        short = re.sub(r"^[a-z]{2}-[A-Z]{2}-(?:[a-z]+-)?|(?:Multilingual)?Neural$", "", vid)
        out.update({vid.lower(): vid, cn.lower(): vid, short.lower(): vid})
    out.update({k.lower(): v for k, v in _EXTRA_ALIASES.items()})
    return out


_EDGE_ALIAS = _edge_aliases()
_DS_ALIAS = {}
for _vid, (_cn, _, _) in DS_VOICES.items():
    _DS_ALIAS.update({_vid.lower(): _vid, _cn.lower(): _vid})
    if "-" in _cn:
        _DS_ALIAS[_cn.split("-", 1)[1].lower()] = _vid


# ── 配置 ─────────────────────────────────────────────────────

def dashscope_key() -> str:
    if os.environ.get("DASHSCOPE_API_KEY"):
        return os.environ["DASHSCOPE_API_KEY"].strip()
    try:
        from lib.config import load_cfg
        cfg = load_cfg()
    except Exception:
        return ""
    for sect in ("tts", "asr"):
        k = (cfg.get(sect) or {}).get("dashscope_api_key")
        if k:
            return str(k).strip()
    for m in cfg.get("models") or []:
        if "dashscope" in str(m.get("backend_url", "")) and m.get("api_key"):
            return str(m["api_key"]).strip()
    return ""


# ── 音色 / 参数 ──────────────────────────────────────────────

def engine_order() -> list[str]:
    try:
        from lib.config import load_cfg
        order = (load_cfg().get("tts") or {}).get("engines")
    except Exception:
        order = None
    order = [str(e).lower() for e in (order or ["local", "edge", "dashscope"]) if str(e).lower() in ENGINES]
    return order or ["edge"]


def _local_ok() -> bool:
    try:
        from tts_local import available
    except Exception:
        try:
            from core.tts_local import available
        except Exception:
            return False
    return available()


def _local_mod():
    try:
        import tts_local
    except Exception:
        from core import tts_local
    return tts_local


def plan(voice: str | None, engine: str = "auto", has_ds_key: bool | None = None,
         local_ok: bool | None = None) -> list[tuple[str, str, str]]:
    """→ 依次尝试的 [(引擎, 音色 id, 提示)], 第一个失败就用下一个。
    本地认得的音色 (空/女声1/晓晓…) 按引擎顺序; 本地没有的 (方言/粤语/英文/百炼独有) 在线优先。"""
    engine = (engine or "auto").strip().lower()
    key_ok = dashscope_key() != "" if has_ds_key is None else has_ds_key
    loc_ok = _local_ok() if local_ok is None else local_ok
    online = resolve_voice(voice, engine if engine in ("edge", "dashscope") else "auto", has_ds_key=key_ok)
    loc = None
    if loc_ok:
        sid, _ = _local_mod().resolve(voice)
        if sid is not None:
            loc = ("local", str(sid), "")
    if engine == "local":
        if loc:
            return [loc, online]
        why = "本地引擎未安装" if not loc_ok else f"本地引擎没有音色 {voice}"
        return [(online[0], online[1], f"{why}, 改用 {ENGINE_NAMES[online[0]]}。 {online[2]}".strip())]
    if engine in ("edge", "dashscope"):
        return [online] + ([loc] if loc else [])
    out: list[tuple[str, str, str]] = []
    for e in engine_order():
        if e == "local" and loc:
            out.append(loc)
        elif e == online[0] and online not in out:
            out.append(online)
    if online not in out:
        out.append(online)
    if online[0] == "dashscope" and ("edge", DEFAULT_EDGE_VOICE, "") not in out:
        out.append(("edge", DEFAULT_EDGE_VOICE, "改用 edge-tts 晓晓。"))
    return out


def resolve_voice(voice: str | None, engine: str = "auto", has_ds_key: bool | None = None) -> tuple[str, str, str]:
    """在线引擎的音色解析 → (引擎, 音色 id, 提示)。voice 可以是中文名 (晓晓/芊悦)、短名 (xiaoxiao/Cherry) 或原始 id。"""
    engine = (engine or "auto").strip().lower()
    if engine not in ("auto", "edge", "dashscope"):
        engine = "auto"
    v = str(voice or "").strip()
    key_ok = dashscope_key() != "" if has_ds_key is None else has_ds_key
    low = v.lower()
    ds = _DS_ALIAS.get(low)
    edge = _EDGE_ALIAS.get(low) or (v if re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[\w-]+Neural", v) else None)
    note = ""
    if engine == "dashscope":
        if not key_ok:
            engine, note = "edge", "没有百炼 key, 改用 edge-tts。"
        else:
            if ds:
                return "dashscope", ds, ""
            return "dashscope", DEFAULT_DS_VOICE, (f"百炼没有音色 {v}, 用 {DEFAULT_DS_VOICE}(芊悦)。" if v else "")
    if engine == "auto" and ds and not edge:
        if key_ok:
            return "dashscope", ds, ""
        note = f"{v} 是百炼音色, 但没有百炼 key, 改用 edge-tts 晓晓。"
        return "edge", DEFAULT_EDGE_VOICE, note
    if edge:
        return "edge", edge, note
    if not v:
        return "edge", DEFAULT_EDGE_VOICE, note
    fb = EDGE_FALLBACK.get(v)
    if fb:
        return "edge", fb, note + f"edge-tts 不提供 {v}, 用最接近的 {EDGE_VOICES[fb][0]}。"
    return "edge", DEFAULT_EDGE_VOICE, note + f"未知音色 {v}, 用晓晓; 可用 action=voices 查看音色列表。"


def _signed(val, unit: str, scale: float = 1.0, lo: int = -90, hi: int = 200) -> str:
    """'+10%' / '10' / -5 / 1.2(倍速) → edge-tts 需要的 '+10%' / '-5Hz' 写法。"""
    if val is None or str(val).strip() == "":
        return f"+0{unit}"
    s = str(val).strip().lower().replace(unit.lower(), "").replace("%", "").replace("hz", "")
    try:
        x = float(s)
    except ValueError:
        return f"+0{unit}"
    if unit == "%" and 0 < x <= 3 and "." in s and not s.startswith(("+", "-")):   # 1.2 → 倍速 +20%
        x = (x - 1) * 100
    x = int(round(max(lo, min(hi, x * scale))))
    return f"{x:+d}{unit}"


def prosody(rate=None, pitch=None, volume=None) -> dict:
    return {"rate": _signed(rate, "%"), "volume": _signed(volume, "%", lo=-100, hi=100),
            "pitch": _signed(pitch, "Hz", lo=-100, hi=100)}


# ── 切分 ─────────────────────────────────────────────────────

_SENT_END = re.compile(r"(?<=[。！？!?；;…])|(?<=\.)\s+|\n+")
_SOFT = re.compile(r"(?<=[，,、：:])")


def split_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """按句切, 相邻短句合并到 ≤limit 字; 单句超长再按逗号, 仍超长硬切。"""
    text = re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()
    if not text:
        return []
    pieces: list[str] = []
    for sent in (p.strip() for p in _SENT_END.split(text)):
        if not sent:
            continue
        if len(sent) <= limit:
            pieces.append(sent)
            continue
        for sub in (p.strip() for p in _SOFT.split(sent)):
            while len(sub) > limit:
                pieces.append(sub[:limit])
                sub = sub[limit:]
            if sub:
                pieces.append(sub)
    chunks: list[str] = []
    for p in pieces:
        if chunks and len(chunks[-1]) + len(p) + 1 <= limit:
            sep = " " if re.match(r"[A-Za-z0-9]", p) and re.search(r"[A-Za-z0-9.,!?]$", chunks[-1]) else ""
            chunks[-1] += sep + p
        else:
            chunks.append(p)
    return [c for c in chunks if re.search(r"\w", c)]


# ── 合成 ─────────────────────────────────────────────────────

async def _edge_one(text: str, voice: str, pros: dict, out: Path) -> None:
    import edge_tts
    last = None
    for attempt in range(3):
        try:
            await edge_tts.Communicate(text, voice, rate=pros["rate"], volume=pros["volume"],
                                       pitch=pros["pitch"]).save(str(out))
            if out.exists() and out.stat().st_size > 0:
                return
            last = RuntimeError("edge-tts 没有返回音频 (文本可能只有符号)")
        except Exception as e:   # 网络抖动 / 服务限流: 退避重试
            last = e
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"edge-tts 合成失败: {type(last).__name__}: {last}")


async def _dashscope_one(text: str, voice: str, key: str, out: Path) -> None:
    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as c:
        r = await c.post(DS_URL, headers={"Authorization": f"Bearer {key}"},
                         json={"model": DS_MODEL, "input": {"text": text, "voice": voice}})
        if r.status_code != 200:
            raise RuntimeError(f"百炼 TTS HTTP {r.status_code}: {r.text[:200]}")
        url = (((r.json().get("output") or {}).get("audio") or {}).get("url")) or ""
        if not url:
            raise RuntimeError(f"百炼 TTS 没返回音频地址: {r.text[:200]}")
        a = await c.get(url)
        if a.status_code != 200 or not a.content:
            raise RuntimeError(f"下载百炼音频失败 HTTP {a.status_code}")
        out.write_bytes(a.content)


async def _local_one(text: str, sid: str, pros: dict, out: Path) -> None:
    await asyncio.to_thread(_local_mod().synth_wav, text, int(sid), out, pros.get("rate"))


ENGINES = ("local", "edge", "dashscope")


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {r.stderr.strip()[-300:]}")


_CODEC = {"mp3": ["-c:a", "libmp3lame", "-q:a", "4"], "wav": ["-c:a", "pcm_s16le"],
          "m4a": ["-c:a", "aac", "-b:a", "128k"]}


def concat(parts: list[Path], out: Path, fmt: str) -> None:
    """逐段音频拼成一个文件并转码 (各段采样率不同也行: concat 滤镜前统一重采样)。"""
    if not shutil.which("ffmpeg"):
        if len(parts) == 1 and parts[0].suffix.lstrip(".") == fmt:
            shutil.copyfile(parts[0], out)
            return
        raise RuntimeError("没有 ffmpeg, 无法拼接/转码音频")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    n = len(parts)
    flt = "".join(f"[{i}:a]aresample=24000,aformat=channel_layouts=mono[a{i}];" for i in range(n))
    flt += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    _run(cmd + ["-filter_complex", flt, "-map", "[out]", *_CODEC[fmt], str(out)])


def duration(p: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return round(float(out or 0), 2)
    except Exception:
        return 0.0


def provider_tts() -> dict | None:
    """「能力接入」里启用的配音卡 (默认在前) 中第一张提供 OpenAI 兼容 /v1/audio/speech 的。
    2026-10-23 实测: 用户已把自建 IndexTTS 设为默认配音, text_to_speech 却只认 local/edge/百炼。"""
    import re as _re
    try:
        from lib.providers import load_all
        cards = [c for c in load_all() if c.get("kind") == "tts" and c.get("enabled", True)]
    except Exception:
        return None
    for c in sorted(cards, key=lambda c: not c.get("default")):
        m = _re.search(r"(https?://[^\s'\"]+?/v1)/audio/speech", c.get("usage") or "")
        base = m.group(1) if m else (c.get("base_url") or "").rstrip("/") if c.get("protocol") == "openai_tts" else ""
        if base:
            v = _re.search(r'"voice"\s*:\s*"([^"]+)"', c.get("usage") or "")
            return {"name": c.get("name", ""), "base": base, "key": c.get("api_key") or "",
                    "voice": (v.group(1) if v else "") or c.get("model") or "alloy"}
    return None


async def _provider_one(text: str, card: dict, rate, out: Path) -> None:
    speed = 1.0
    try:
        if isinstance(rate, (int, float)):
            speed = float(rate)
        elif isinstance(rate, str) and rate.strip().endswith("%"):
            speed = 1 + float(rate.strip().rstrip("%")) / 100
    except ValueError:
        pass
    hdr = {"Content-Type": "application/json"}
    if card["key"] and card["key"] != "EMPTY":
        hdr["Authorization"] = f"Bearer {card['key']}"
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"{card['base']}/audio/speech", headers=hdr,
                         json={"input": text, "voice": card["voice"], "speed": max(0.5, min(speed, 2.0)),
                               "response_format": "wav"})
    if r.status_code != 200 or len(r.content) < 1000:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    out.write_bytes(r.content)


async def synthesize(text: str, out_dir: Path, *, voice: str | None = None, engine: str = "auto",
                     rate=None, pitch=None, volume=None, fmt: str = "mp3", stem: str = "") -> dict:
    """合成到 out_dir/<stem>.<fmt>, 返回 {path, duration, engine, voice, chunks, note}。"""
    fmt = (fmt or "mp3").lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"format 只支持 {'/'.join(FORMATS)}")
    text = str(text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"文本 {len(text)} 字, 超过单次上限 {MAX_TEXT_CHARS} 字; 请分段调用")
    key = dashscope_key()
    candidates = plan(voice, engine, has_ds_key=bool(key))
    card = provider_tts() if engine in ("auto", "provider") else None
    if card:   # 能力接入里的默认配音卡最先试; 失败再走本地 / edge / 百炼
        candidates = [("provider", card["voice"], "")] + (candidates if engine == "auto" else [])
    pros = prosody(rate, pitch, volume)
    chunks = split_text(text)
    if not chunks:
        raise ValueError("text 里没有可朗读的文字")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"tts_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid() % 1000:03d}"
    out = out_dir / f"{stem}.{fmt}"
    notes, errors = [], []
    for eng, vid, note in candidates:
        if note:
            notes.append(note)
        if eng == "dashscope" and any(pros[k] not in ("+0%", "+0Hz") for k in ("pitch", "volume", "rate")):
            notes.append("百炼音色不支持语速/音调/音量调节, 已忽略。")
        sem = asyncio.Semaphore(1 if eng in ("local", "provider") else 3)
        try:
            with tempfile.TemporaryDirectory(prefix="tts_") as td:
                ext = "mp3" if eng == "edge" else "wav"
                parts = [Path(td) / f"{i:04d}.{ext}" for i in range(len(chunks))]

                async def one(i: int):
                    async with sem:
                        if eng == "provider":
                            await _provider_one(chunks[i], card, rate, parts[i])
                        elif eng == "dashscope":
                            await _dashscope_one(chunks[i], vid, key, parts[i])
                        elif eng == "local":
                            await _local_one(chunks[i], vid, pros, parts[i])
                        else:
                            await _edge_one(chunks[i], vid, pros, parts[i])
                await asyncio.gather(*(one(i) for i in range(len(chunks))))
                await asyncio.to_thread(concat, parts, out, fmt)
        except Exception as e:
            errors.append(f"{eng}: {e}")
            notes.append(f"{ENGINE_NAMES.get(eng, eng)} 合成失败, 换下一个引擎。")
            continue
        shown = (_local_mod().VOICES.get(int(vid), (vid,))[0] if eng == "local"
                 else f"{card['name']} · {vid}" if eng == "provider" else vid)
        return {"path": str(out), "duration": await asyncio.to_thread(duration, out), "engine": eng,
                "voice": shown, "chunks": len(chunks), "chars": len(text), "note": " ".join(notes).strip()}
    raise RuntimeError("所有语音引擎都失败: " + " | ".join(errors))


ENGINE_NAMES = {"local": "本地引擎", "edge": "edge-tts", "dashscope": "百炼", "provider": "能力接入"}


def voices_text(has_ds_key: bool | None = None) -> str:
    key_ok = dashscope_key() != "" if has_ds_key is None else has_ds_key
    lines = [f"引擎顺序 (engine=auto): {' → '.join(engine_order())}"]
    if _local_ok():
        lines.append("本地引擎音色 (纯 CPU, 不联网, 默认; engine=local):")
        lines += [f"  {cn:<7} {g} {style}" for sid, (cn, g, style) in _local_mod().VOICES.items()]
    else:
        lines.append("本地引擎: 未安装模型 (不可用)")
    lines.append("edge-tts 音色 (免 key, 需联网; voice 填中文名或 id 均可):")
    lines += [f"  {cn:<7} {g} {style:<16} {vid}" for vid, (cn, g, style) in EDGE_VOICES.items()]
    lines.append(f"百炼 qwen3-tts-flash 音色 ({'已配置 key, 可用' if key_ok else '未配置百炼 key, 不可用'}; engine=dashscope 或直接填音色名):")
    lines += [f"  {cn:<8} {g} {style:<12} {vid}" for vid, (cn, g, style) in DS_VOICES.items()]
    lines.append("语速/音量: '+20%' / '-10%' (或 1.2 倍速); 音调: '+5Hz'。只有 edge-tts 支持。")
    return "\n".join(lines)

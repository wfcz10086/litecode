#!/usr/bin/env python3
"""anime_short.py — 动漫短片 / MV 一条龙: Qwen-Image 画首帧 → MiniMax H3 (+ AniSora 并行) 动起来 →
IndexTTS 配音 或 ACE-Step 歌曲 → ffmpeg 合成 + 字幕。服务地址都从「能力接入」卡片取。

  anime_short.py check                         # 各服务是否在线
  anime_short.py mv    plan.json -o 输出目录    # MV: 歌切段, 每段 H3 参考生视频 (参考图 + 这段歌), 拼接后换回原曲 + 歌词字幕
  anime_short.py story plan.json -o 输出目录    # 剧情: 每个镜头图生视频 + 角色台词配音 + 字幕 (+ 可选配乐)

已经做好的首帧 / 视频片段 / 配音会跳过 (断点续跑)。每步都有超时; 单个镜头失败先换引擎重试, 再失败用首帧慢推镜头顶上, 报告里写明。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HOME = Path(os.environ.get("LITECODE_HOME", "/opt/litecode"))
COMFY = HOME / "skills/comfyui-image/scripts/comfy.py"
SONG = HOME / "skills/ai-song/scripts/song.py"
CFG_PATHS = [os.environ.get("LITECODE_CONFIG", ""), str(HOME / "config.json")]
SEG = 124 / 24            # H3 一段: 124 帧 @24fps ≈ 5.17 秒 (帧数须 17n+5)
W, H = 720, 1280          # 成片竖版
WARN: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


# ── 服务地址 (能力接入卡片) ─────────────────────────────────────
def _cards() -> list[dict]:
    for p in CFG_PATHS:
        if p and Path(p).is_file():
            try:
                cfg = json.loads(Path(p).read_text(encoding="utf-8"))
                return [c for c in cfg.get("providers") or [] if isinstance(c, dict) and c.get("enabled", True)]
            except Exception:
                pass
    return []


def _find(env: str, pat: str, url_pat: str = "") -> str:
    if os.environ.get(env):
        return os.environ[env].rstrip("/")
    for c in sorted(_cards(), key=lambda c: not c.get("default")):
        text = f"{c.get('name', '')} {c.get('note', '')}"
        if re.search(pat, text, re.I):
            if url_pat:
                m = re.search(url_pat, c.get("usage") or "")
                if m:
                    return m.group(1)
            if c.get("base_url"):
                return c["base_url"].rstrip("/")
    return ""


def self_url(pat: str) -> str:
    """卡片 extra.self_url: ComfyUI 服务器访问自己的地址 (喂给 *FromURL 节点; 经转发/代理访问时必须设)。"""
    for c in _cards():
        if re.search(pat, f"{c.get('name', '')} {c.get('note', '')}", re.I):
            return str((c.get("extra") or {}).get("self_url") or "")
    return ""


def services() -> dict:
    return {"image": _find("ANIME_IMAGE_URL", r"qwen[- ]?image"),
            "h3": _find("ANIME_H3_URL", r"\bH3\b"),
            "anisora": _find("ANIME_ANISORA_URL", r"anisora", r"(https?://[^\s'\"]+?)/v1/videos"),
            "tts": _find("ANIME_TTS_URL", r"indextts|配音", r"(https?://[^\s'\"]+?/v1)/audio/speech"),
            "asr": _find("ANIME_ASR_URL", r"sensevoice|语音识别", r"(https?://[^\s'\"]+?/v1)/audio/transcriptions")}


def _alive(url: str, path: str) -> bool:
    try:
        with urllib.request.urlopen(url + path, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def check() -> dict:
    s = services()
    paths = {"image": "/system_stats", "h3": "/system_stats", "anisora": "/health", "tts": "/../health", "asr": "/models"}
    st = {}
    for k, u in s.items():
        p = paths[k]
        if k == "tts" and u.endswith("/v1"):
            ok = _alive(u[:-3], "/health")
        elif k == "asr":
            ok = bool(u)
        else:
            ok = bool(u) and _alive(u, p)
        st[k] = {"url": u, "ok": ok}
    return st


# ── 小工具 ─────────────────────────────────────────────────────
def run(cmd: list, timeout: int, what: str) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what} 超过 {timeout}s")
    if r.returncode != 0:
        raise RuntimeError(f"{what} 失败: {(r.stderr or r.stdout).strip()[-500:]}")
    return r.stdout + r.stderr


def ff(args: list, timeout: int = 600, what: str = "ffmpeg") -> None:
    run(["ffmpeg", "-v", "error", "-y", *args], timeout, what)


def dur(p: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], 60, "ffprobe")
    return float(out.strip().splitlines()[0])


def has_audio(p: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=60).stdout
    return bool(out.strip())


def comfy(base: str, wf: str, prompt: str, inputs: dict, out_dir: Path, prefix: str, extra: list,
          timeout: int = 900) -> Path:
    su = self_url(r"\bH3\b") if "h3_" in wf else self_url(r"qwen[- ]?image")
    cmd = [sys.executable, str(COMFY)] + (["--self-url", su] if su else []) + ["run", base, "-w", wf, "--prompt", prompt, "-o", str(out_dir),
           "--prefix", prefix, "--timeout", str(timeout), "--idle-timeout", "600", *extra]
    for k, v in inputs.items():
        cmd += ["--input", f"{k}={v}"]
    try:
        out = run(cmd, timeout + 700, f"ComfyUI {wf}")
    except RuntimeError as e:   # 网络抖动 / 连接被拒 / 5xx: 同一服务先重试一次, 再交给上层换引擎
        if not re.search(r"timed out|timeout|Connection|URLError|HTTP 5\d\d|Remote end closed|超过", str(e), re.I):
            raise
        log(f"[重试] {wf} 临时错误, 10 秒后重试一次: {str(e)[:120]}")
        time.sleep(10)
        out = run(cmd, timeout + 700, f"ComfyUI {wf} (重试)")
    m = re.search(r"RESULT (\{.*\})", out)
    files = json.loads(m.group(1)).get("files") if m else None
    if not files:
        raise RuntimeError(f"ComfyUI {wf} 没有产出文件: {out.strip()[-300:]}")
    return Path(files[-1]["path"])


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    bnd = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, p in files.items():
        parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k.strip()}"; filename="{Path(p).name}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n'.encode() + Path(p).read_bytes() + b"\r\n")
    parts.append(f"--{bnd}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={bnd}"


def anisora(base: str, image: Path, prompt: str, out: Path, timeout: int = 1200, last: Path | None = None) -> Path:
    fields = {"prompt": prompt, "size": "480*832", "frame_num": "81", "steps": "10"}
    files: dict = {"image": image}
    if last:
        fields["positions"] = "0,1"
        files = {"image": image, "image ": last}   # 同名字段两次 (首帧 + 尾帧); 第二个键多一个空格只为区分, 发送时会去掉
    body, ctype = _multipart(fields, files)
    req = urllib.request.Request(f"{base}/v1/videos", data=body, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        jid = json.loads(r.read().decode())["id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(10)
        with urllib.request.urlopen(f"{base}/v1/videos/{jid}", timeout=30) as r:
            st = json.loads(r.read().decode())
        if st.get("status") == "succeeded":
            with urllib.request.urlopen(f"{base}/v1/videos/{jid}/content", timeout=300) as r, open(out, "wb") as f:
                f.write(r.read())
            return out
        if st.get("status") == "failed" or (st.get("status") is None and time.time() - t0 > 60):
            raise RuntimeError(f"AniSora 失败: {json.dumps(st, ensure_ascii=False)[:300]}")
    raise RuntimeError(f"AniSora 超过 {timeout}s 没完成 (任务 {jid})")


def kenburns(image: Path, out: Path, seconds: float) -> Path:
    """兜底: 首帧慢推镜头。"""
    n = int(seconds * 24) + 1
    ff(["-loop", "1", "-i", str(image), "-vf",
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
        f"zoompan=z='min(zoom+0.0012,1.15)':d={n}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=24",
        "-t", f"{seconds:.3f}", "-pix_fmt", "yuv420p", str(out)], what="慢推镜头")
    return out


# ── 识图审阅 (能力接入之外: 用设置里的识图模型, 如 Qwen3.6) ─────────
def _vision_model() -> dict | None:
    for p in CFG_PATHS:
        if p and Path(p).is_file():
            try:
                cfg = json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception:
                continue
            vid = cfg.get("vision_fallback_id") or cfg.get("default_model_id")
            for m in cfg.get("models") or []:
                if m.get("id") == vid and m.get("supports_vision"):
                    return m
    return None


def ask_vision(images: list[Path], ask: str, schema: str) -> dict | None:
    """把图给识图模型, 要它只回一行 JSON (schema 是示例); 不可用 / 出错返回 None。"""
    import base64
    m = _vision_model()
    if not m or os.environ.get("ANIME_NO_REVIEW"):
        return None
    content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(
        _thumb(p).read_bytes()).decode()}} for p in images[:10]]
    content.append({"type": "text", "text": f"{ask}\n只输出一行 JSON: {schema}"})
    body = {"model": m.get("served_model_name") or m["id"], "max_tokens": 400, "temperature": 0,
            "messages": [{"role": "user", "content": content}], "chat_template_kwargs": {"enable_thinking": False}}
    hdr = {"Content-Type": "application/json"}
    if m.get("api_key") and m["api_key"] != "EMPTY":
        hdr["Authorization"] = f"Bearer {m['api_key']}"
    try:
        req = urllib.request.Request(m["backend_url"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=150) as r:
            text = json.loads(r.read().decode())["choices"][0]["message"]["content"] or ""
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception as e:
        WARN.append(f"识图出错, 跳过这次检查: {str(e)[:120]}")
        return None


def review(images: list[Path], ask: str) -> tuple[bool, str]:
    """让识图模型看图打分: 返回 (是否合格, 问题说明)。模型不可用 / 出错时算合格 (只记警告, 不卡流程)。"""
    import base64
    m = _vision_model()
    if not m or os.environ.get("ANIME_NO_REVIEW"):
        return True, "跳过审阅 (没有识图模型)"
    content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(
        _thumb(p).read_bytes()).decode()}} for p in images]
    content.append({"type": "text", "text": ask + "\n只输出一行 JSON: {\"ok\": true/false, \"problems\": \"不合格时写具体问题, 合格写空串\"}"})
    body = {"model": m.get("served_model_name") or m["id"], "max_tokens": 300, "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "chat_template_kwargs": {"enable_thinking": False}}
    hdr = {"Content-Type": "application/json"}
    if m.get("api_key") and m["api_key"] != "EMPTY":
        hdr["Authorization"] = f"Bearer {m['api_key']}"
    try:
        req = urllib.request.Request(m["backend_url"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=120) as r:
            text = json.loads(r.read().decode())["choices"][0]["message"]["content"] or ""
        j = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        return bool(j.get("ok")), str(j.get("problems") or "")
    except Exception as e:
        WARN.append(f"识图审阅出错, 跳过: {str(e)[:120]}")
        return True, "审阅出错, 跳过"


def _thumb(p: Path) -> Path:
    t = p.parent / f".rv_{p.stem}.jpg"
    ff(["-i", str(p), "-vf", "scale=512:-2", "-q:v", "4", str(t)], 60, "缩略图")
    return t


def clip_shots(video: Path, n: int = 3) -> list[Path]:
    d = dur(video)
    out = []
    for k in range(n):
        f = video.parent / f".rv_{video.stem}_{k}.jpg"
        ff(["-ss", f"{d * (k + 0.5) / n:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=512:-2", str(f)],
           60, "抽帧")
        out.append(f)
    return out


# ── 首帧 ───────────────────────────────────────────────────────
LOOKS = {   # 画风开关: plan["look"]; style 没写时用这里的默认画风
    "anime": "日系动漫电影画风, 赛璐璐上色, 电影感光影, 竖版构图",
    "real": "电影级写实摄影, 真人实拍质感, 自然肤色和皮肤纹理, 35mm 镜头, 浅景深, 电影调色, 竖版构图, 不要动漫/插画/卡通风格",
}


def look_of(plan: dict) -> str:
    """画风开关, 默认写实 (2026-10-23 用户: "一般都是默认写实版"); 明确写 anime / 动漫 才出动漫。"""
    lk = str(plan.get("look") or "real").lower()
    return "anime" if lk in ("anime", "动漫", "二次元", "国漫", "cartoon") else "real"


def style_of(plan: dict) -> str:
    st = plan.get("style") or LOOKS[look_of(plan)]
    return st if look_of(plan) == "anime" else st + ", " + LOOKS["real"] if "写实" not in st else st


# ── 提示词优化 (大模型): 生成前把画面描述改写成生图 / 视频模型听得懂的具体提示词; 审阅不合格时按意见重写 ──
def _main_model() -> dict | None:
    for p in CFG_PATHS:
        if p and Path(p).is_file():
            try:
                cfg = json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception:
                continue
            mid = cfg.get("default_model_id")
            for m in cfg.get("models") or []:
                if m.get("id") == mid:
                    return m
    return None


def llm_json(system: str, user: str, timeout: int = 180) -> dict | list | None:
    """调主力模型, 要它只回 JSON; 失败返回 None (流程不因此中断)。"""
    m = _main_model()
    if not m or os.environ.get("ANIME_NO_LLM"):
        return None
    body = {"model": m.get("served_model_name") or m["id"], "temperature": 0.4, "max_tokens": 4000,
            "reasoning_effort": "low",   # GLM 默认长思考, 改写提示词用低档 (长翻译实测思考 token 降到接近 0, 质量不变)
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    hdr = {"Content-Type": "application/json"}
    if m.get("api_key") and m["api_key"] != "EMPTY":
        hdr["Authorization"] = f"Bearer {m['api_key']}"
    try:
        req = urllib.request.Request(m["backend_url"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = json.loads(r.read().decode())["choices"][0]["message"]["content"] or ""
        mm = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        return json.loads(mm.group(1)) if mm else None
    except Exception as e:
        WARN.append(f"提示词优化调用大模型出错, 用原描述: {str(e)[:120]}")
        return None


PROMPT_RULES = """你是 AI 生图 / 生视频的提示词工程师。把分镜描述改写成模型能稳定画对的提示词:
1. 只写画得出来的具体东西: 主体、外观、动作、场景、景别、构图 (竖版 9:16)、光线、色调; 抽象概念换成具体视觉 (例 "导弹礼物" → "金色卡通导弹形状的礼物图标, 带火焰拖尾光效")。
2. 画面里尽量不要文字、字幕、屏幕界面、弹幕文字 (模型画字会乱码); 需要时换成视觉效果 (例 "弹幕刷屏" → "画面两侧飘过许多彩色发光小条带")。
3. **原描述里的主体、动作、表情、关键道具一个都不能删、不能换** (例 "对着镜头竖起大拇指" 必须保留), 只是写得更具体。
   有主角 (cast=true) 的镜头: 主角正面或 3/4 侧面、脸清楚可见 (不许改成背影 / 虚化 / 剪影 / 看不清脸), 写清位置和动作, 外貌按给定人物描述;
   cast=false 的镜头不要出现主角。
4. 一个镜头一个主体, 别塞太多元素; 和画风保持一致 (look=real 是真人写实摄影, anime 是动漫)。
5. 视频提示词 (motion) 写这 5 秒里的动作和镜头运动: **从首帧的姿势自然延续, 保留首帧的手势和表情, 一个镜头只做一个主要动作,
   不要中途换手势或另加动作** (例 首帧竖大拇指 → 保持竖大拇指, 轻轻点头微笑; 不要放下手再挥手)。
   节奏强弱只体现在镜头运动和特效上 (强: 镜头快推拉、特效强; 弱: 慢镜头、舒缓), 不是让人物多做动作。
只输出 JSON。"""


def optimize_prompts(plan: dict, out: Path) -> None:
    """生成前: 全部镜头一次交给大模型改写, 结果写回 shot["_img"] / shot["_motion"], 缓存在 prompts.json。"""
    cache = out / "prompts.json"
    key = hashlib.sha1(json.dumps([plan.get("look"), plan.get("style"), plan.get("character"), plan["shots"]],
                                  ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    data = None
    if cache.exists():
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            data = c["shots"] if c.get("key") == key else None
        except Exception:
            data = None
    if data is None:
        shots = [{"no": i, "image": sh.get("image", ""), "motion": sh.get("motion", ""), "lyric": sh.get("lyric", ""),
                  "kind": sh.get("kind", ""), "cast": has_cast(plan, sh)} for i, sh in enumerate(plan["shots"], 1)]
        user = json.dumps({"look": look_of(plan), "style": style_of(plan), "character": plan.get("character", ""),
                           "shots": shots}, ensure_ascii=False)
        data = llm_json(PROMPT_RULES + '\n输出: [{"no": 1, "image": "生图提示词", "motion": "视频提示词"}, ...] 每个镜头一条。', user)
        if not isinstance(data, list):
            log("[提示词] 大模型优化失败, 用原描述")
            return
        cache.write_text(json.dumps({"key": key, "shots": data}, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"[提示词] 已优化 {len(data)} 个镜头")
    for d in data:
        try:
            sh = plan["shots"][int(d["no"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if d.get("image"):
            sh["_img"] = str(d["image"])
        if d.get("motion"):
            sh["_motion"] = str(d["motion"])


def rewrite_prompt(plan: dict, shot: dict, kind: str, current: str, problems: str) -> str:
    """审阅不合格: 把审阅意见交给大模型重写提示词 (kind = 生图 / 视频)。失败就在原提示词后补注意事项。"""
    d = llm_json(PROMPT_RULES + '\n输出: {"prompt": "重写后的提示词"}',
                 json.dumps({"look": look_of(plan), "style": style_of(plan), "character": plan.get("character", ""),
                             "cast": has_cast(plan, shot), "要画的": shot.get("image", ""), "唱词": shot.get("lyric", ""),
                             "类型": kind, "上一次的提示词": current, "审阅发现的问题": problems}, ensure_ascii=False))
    new = (d or {}).get("prompt") if isinstance(d, dict) else None
    if new:
        log(f"[提示词] 按审阅意见重写 ({kind}): {new[:60]}…")
        return str(new)
    return f"{current}。注意避免: {problems}"


def char_ref(plan: dict, out: Path, svc: dict) -> Path | None:
    """主角定妆照: 之后每个镜头都以它为参考图来画, 保证人物一致。
    plan["ref_image"] = 用户给的人物照片: 直接当定妆照 (人物就是照片里的人), 不再自己画。"""
    f = out / "frames" / "ref.png"
    if plan.get("ref_image"):
        src = Path(plan["ref_image"])
        if not src.exists():
            raise RuntimeError(f"ref_image 不存在: {src}")
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            ff(["-i", str(src), "-frames:v", "1", str(f)], 60, "定妆照")   # 统一转成 png
            log(f"[定妆] 用用户给的照片: {src.name}")
        return f
    if not plan.get("character"):
        return None
    if f.exists():
        return f
    prompt = f"{style_of(plan)}，角色设定图，单人，正面半身像，五官清晰，纯色浅灰背景，{plan['character']}"
    ask = (f"这是动漫短片主角的定妆照。人物要求「{plan['character']}」, 画风「{style_of(plan)}」。"
           "检查: 是否单人、正面、五官清楚、符合人物要求和画风、有没有畸形或乱码文字。")
    for attempt in (1, 2):
        p = comfy(svc["image"], "qwen21_t2i", prompt, {}, out / "frames" / "raw", "ref",
                  ["--width", "768", "--height", "1376"], timeout=600)
        ok, why = review([p], ask)
        log(f"[定妆] 第 {attempt} 次 审阅: {'✓' if ok else '✗ ' + why}")
        if ok:
            break
        prompt += f"。注意避免: {why}"
    shutil.copy(p, f)
    return f


_PEOPLE = re.compile(r"他|她|主角|人物|男人|女人|男孩|女孩|少年|少女|大叔|大爷|大妈|阿姨|老人|小伙|姑娘|歌手|舞者")   # 观众/人群不算主角


def has_cast(plan: dict, shot: dict) -> bool:
    """这个镜头里有没有主角: shot["cast"] 明写为准; 否则看画面描述里有没有人物词 / 人物外貌里的关键词。
    (2026-10-23《吹喇叭》: "油条下锅特写" 也套了定妆照, 画成了大叔半身像)"""
    if "cast" in shot:
        return bool(shot["cast"])
    text = shot.get("image", "")
    keys = [w for w in re.split(r"[,，、\s]+", plan.get("character", "")) if 1 < len(w) <= 4]
    return bool(_PEOPLE.search(text)) or any(k in text for k in keys)


def _draw(plan: dict, shot: dict, i: int, d: Path, svc: dict, ref: Path | None, extra_note: str = "",
          use_ref: bool = True) -> Path:
    scene = f"{shot.get('_img') or shot['image']}{('。' + extra_note) if extra_note else ''}"
    cast = has_cast(plan, shot)
    if ref and cast and use_ref:   # 以定妆照为参考图画新场景 (改图模式); 画风放最前, 防止被带成写实
        want = "真人写实照片" if look_of(plan) == "real" else "动漫画风, 不要写实照片, 不要 3D 渲染"
        try:
            return comfy(svc["image"], "qwen21_edit",
                         f"画风: {style_of(plan)}, {want}。保持参考图中这个人物的长相、脸型、发型、服装不变, "
                         f"画出新的画面: {scene}。竖版构图",
                         {"image": ref}, d / "raw", f"shot{i:02d}", [], timeout=600)
        except RuntimeError as e:
            WARN.append(f"第 {i} 帧参考图模式失败, 改用文生图: {str(e)[:120]}")
    prompt = "，".join(x for x in (style_of(plan), plan.get("character") if cast else "", scene) if x)
    return comfy(svc["image"], "qwen21_t2i", prompt, {}, d / "raw", f"shot{i:02d}",
                 ["--width", "768", "--height", "1376"], timeout=600)


def frames(plan: dict, out: Path, svc: dict) -> list[Path]:
    d = out / "frames"
    d.mkdir(parents=True, exist_ok=True)
    if not svc["image"]:
        raise RuntimeError("能力接入里没有 Qwen-Image 生图卡 (或设 ANIME_IMAGE_URL)")
    optimize_prompts(plan, out)
    apply_overrides(plan, out)
    ref = char_ref(plan, out, svc)
    want = '真人写实照片, 不能是动漫' if look_of(plan) == 'real' else '动漫画风'
    res = []
    for i, shot in enumerate(plan["shots"], 1):
        f = d / f"{i:02d}.png"
        if not f.exists():
            t0, note = time.time(), ""
            ask = ((f"第一张是主角定妆照, 第二张是第 {i} 个镜头的首帧。" if ref else f"这是第 {i} 个镜头的首帧。")
                   + f"要求: 画面内容「{shot['image']}」; 画风「{style_of(plan)}」(必须是{want})。检查: 是否符合要求和画风、"
                   + ("人物是不是和定妆照同一个人 (脸型/发型/服装)、" if ref else "")
                   + "人物有没有畸形 (多手指/扭曲的脸/多余肢体)、画面里有没有乱码文字。")
            cast = has_cast(plan, shot)
            if not cast:   # 没有主角的镜头 (物件特写 / 空镜): 不比对定妆照
                ask = (f"这是第 {i} 个镜头的首帧 (这个镜头里没有主角)。要求: 画面内容「{shot['image']}」; 画风「{style_of(plan)}」"
                       f"(必须是{want})。检查: 画面主体是否就是要求的内容、画风是否符合、有没有乱码文字。")
            for attempt in (1, 2):
                # 第 2 次: 参考图模式画风/内容不对时改用文生图 (人物外貌写进提示词), 一致性交给整体比对
                p = _draw(plan, shot, i, d, svc, ref, note, use_ref=(attempt == 1))
                ok, why = review(([ref] if ref and cast else []) + [p], ask)
                log(f"[帧] {i:02d} 第 {attempt} 次 {time.time() - t0:.0f}s 审阅: {'✓' if ok else '✗ ' + why}")
                if ok:
                    break
                shot["_img"] = rewrite_prompt(plan, shot, "生图", shot.get("_img") or shot["image"], why)
                note = ""
            else:
                WARN.append(f"第 {i} 帧两次审阅都不合格, 用了第二次的: {why}")
            shutil.copy(p, f)
        res.append(f)
    for i, shot in enumerate(plan["shots"], 1):   # 尾帧写成画面描述的 (fl2v 转场): 也按定妆照画出来
        tgt = shot.get("last")
        f = d / f"{i:02d}_last.png"
        if tgt and tgt != "next" and not str(tgt).isdigit() and not f.exists():
            shutil.copy(_draw(plan, {"image": str(tgt)}, i, d, svc, ref), f)
            log(f"[帧] {i:02d} 尾帧 (转场用) 已画")
    consistency(plan, res, ref, d, svc)
    return res


def consistency(plan: dict, imgs: list[Path], ref: Path | None, d: Path, svc: dict) -> None:
    """全部首帧画完后整体比对一次: 人物 / 画风不一致的镜头重画一次。"""
    if len(imgs) < 2 or (d / ".consistency_done").exists():
        return
    idx = [i for i, sh in enumerate(plan["shots"], 1) if has_cast(plan, sh)]   # 只比有主角的镜头 (画风另外全看)
    head = "第 1 张是主角定妆照, 后面" if ref else "下面"
    j = ask_vision(([ref] if ref else []) + [imgs[i - 1] for i in idx],
                   f"{head}依次是镜头 {idx} 的首帧 (同一部片子)。找出人物长相/发型/服装和其他镜头 (或定妆照) 明显不一致, "
                   "或画风明显不统一的镜头, 用上面给的镜头编号回答。", '{"bad": [镜头编号, 没有就空列表], "why": "简短原因"}')
    bad = [int(x) for x in (j or {}).get("bad") or [] if str(x).isdigit() and int(x) in idx]
    if bad:
        log(f"[一致性] 不一致的镜头 {bad}: {(j or {}).get('why', '')} → 重画")
        for i in bad:
            p = _draw(plan, plan["shots"][i - 1], i, d, svc, ref, f"人物外貌必须和其他镜头一致: {(j or {}).get('why', '')}")
            shutil.copy(p, imgs[i - 1])
        WARN.append(f"一致性检查重画了镜头 {bad}")
    else:
        log("[一致性] 全部首帧人物和画风一致 ✓" if j else "[一致性] 跳过 (识图模型不可用)")
    (d / ".consistency_done").write_text("1")


# ── 视频片段 (H3 与 AniSora 两条队列并行) ───────────────────────
def _last_of(plan: dict, i: int, imgs: list[Path]) -> Path | None:
    """fl2v 首尾帧的尾帧: shot["last"] = "next" (下一个镜头首帧, 做转场) / 镜头序号 (1 起) /
    一段画面描述 (frames() 已按定妆照画成 frames/NN_last.png)。写了 last 没写 mode 也按 fl2v。"""
    shot = plan["shots"][i]
    tgt = shot.get("last")
    if shot.get("mode") not in (None, "", "fl2v") or (shot.get("mode") != "fl2v" and not tgt):
        return None
    tgt = tgt or "next"
    if tgt == "next" or str(tgt).isdigit():
        k = i + 1 if tgt == "next" else int(tgt) - 1
        return imgs[k] if 0 <= k < len(imgs) else None
    f = imgs[i].parent / f"{i + 1:02d}_last.png"
    return f if f.exists() else None


def h3_size(plan: dict) -> list[str]:
    """默认 480p 竖版 (480x864); 用户明确要 720p / 高清才用 768x1344 (约 2.3 倍耗时)。步数固定 10。"""
    hd = str(plan.get("resolution", "480p")).lower() in ("720p", "720", "hd", "高清", "1080p")
    w, h = ("768", "1344") if hd else ("480", "864")
    return ["--frames", "124", "--width", w, "--height", h, "--steps", "10"]


def clips(jobs: list[dict], svc: dict, out: Path, look: str = "anime", size_args: list | None = None) -> dict:
    d = out / "clips"
    d.mkdir(parents=True, exist_ok=True)
    # AniSora 只会动漫风: 真人写实只用 H3
    engines = [e for e in ("h3", "anisora") if svc[e] and not (e == "anisora" and look == "real")]
    if not engines:
        raise RuntimeError("能力接入里没有可用的视频卡 (MiniMax H3 / AniSora)")
    for k, j in enumerate(jobs):
        want = j.get("engine") or "auto"
        if (j.get("audio") and j.get("mode") in (None, "", "r2v") and not j.get("last")) or j.get("mode") == "t2v":   # 参考音乐 / 文生视频只有 H3 能做
            want = "h3" if svc["h3"] else want
        j["engine"] = want if want in engines else engines[k % len(engines)]
        j["out"] = d / f"{j['idx']:02d}.mp4"

    def one(j: dict) -> tuple[int, Path]:
        if j["out"].exists():
            return j["idx"], j["out"]
        order = [j["engine"]] + [e for e in engines if e != j["engine"]]
        errs = []
        for e in order:
            t0 = time.time()
            try:
                if e == "h3":   # H3 四种模式: r2v 参考图+参考音乐 / i2v 图生 / fl2v 首尾帧 / t2v 文生
                    mode = j.get("mode") or ("fl2v" if j.get("last") else "r2v" if j.get("audio") else "i2v")
                    if mode == "r2v" and j.get("audio"):
                        wf, prm, inp = "h3_r2v", j["h3_prompt"], {"ref_image": j["image"], "ref_audio": j["audio"]}
                    elif mode == "fl2v" and j.get("last"):
                        wf, prm, inp = "h3_fl2v", j["prompt"], {"first": j["image"], "last": j["last"]}
                    elif mode == "t2v":
                        wf, prm, inp = "h3_t2v", j["prompt"], {}
                    else:
                        wf, prm, inp = "h3_i2v", j["prompt"], {"first": j["image"]}
                    p = comfy(svc["h3"], wf, prm, inp, d / "raw", f"seg{j['idx']:02d}", size_args or h3_size({}))
                    shutil.copy(p, j["out"])
                    j["mode_used"] = wf
                else:           # AniSora: 首帧, 有尾帧时首尾两张 (positions 0,1)
                    anisora(svc["anisora"], j["image"], j["prompt"], j["out"], last=j.get("last"))
                ref = j.get("ref") if j.get("cast", True) else None   # 没有主角的镜头 (如配角 / 空镜) 不和定妆照比
                ok, why = review(([ref] if ref else []) + clip_shots(j["out"]),
                                 ("第一张是主角定妆照, 后 3 张" if ref else "这 3 张") + "是一段视频按时间顺序的抽帧。检查: 人物有没有崩坏 "
                                 "(脸变形、多手多脚、身体扭曲)、" + ("人物是否和定妆照同一个人、" if ref else "前后是不是同一个人、")
                                 + "画面有没有明显花屏、"
                                 + f"人物动作是否明显偏离要求 (要求: {str(j.get('prompt', ''))[-120:]}), 例如中途换成别的手势、多出一整个动作。"
                                 + "只有明显问题才判不合格; 手部细节、道具数量、动作幅度这类小瑕疵不算。")
                log(f"[视频] {j['idx']:02d} {e} {time.time() - t0:.0f}s 审阅: {'✓' if ok else '✗ ' + why}")
                if not ok and e != order[-1]:
                    errs.append(f"{e}: 审阅不合格 ({why})")
                    j["out"].unlink(missing_ok=True)
                    continue
                if not ok and j.get("fix") and not j.get("_refixed"):
                    # 没有别的引擎可换: 大模型按审阅意见重写动作提示词, 同一引擎再做一次
                    j["_refixed"] = True
                    j["prompt"] = j["fix"](j["prompt"], why)
                    if j.get("h3_prompt"):
                        j["h3_prompt"] = j["fix"](j["h3_prompt"], why)
                    j["out"].unlink(missing_ok=True)
                    return one(j)
                if not ok:
                    WARN.append(f"第 {j['idx']} 段审阅不合格, 重写提示词后仍不合格, 保留: {why}")
                j["used"] = e
                return j["idx"], j["out"]
            except Exception as ex:
                errs.append(f"{e}: {ex}")
                log(f"[视频] {j['idx']:02d} {e} 失败: {str(ex)[:200]}")
        WARN.append(f"第 {j['idx']} 段视频生成失败, 用首帧慢推镜头顶上 ({'; '.join(errs)[:300]})")
        j["used"] = "kenburns"
        return j["idx"], kenburns(j["image"], j["out"], SEG)

    lanes: dict[str, list] = {e: [] for e in engines}
    for j in jobs:
        lanes[j["engine"]].append(j)
    res = {}
    with cf.ThreadPoolExecutor(max_workers=len(lanes)) as ex:
        futs = [ex.submit(lambda q: [one(j) for j in q], q) for q in lanes.values() if q]
        for f in futs:
            for idx, p in f.result():
                res[idx] = p
    return res


def norm(src: Path, dst: Path, seconds: float, keep_audio: bool = False) -> Path:
    """统一成 720x1280 24fps, 正好 seconds 秒 (短了: 先放慢最多 1.25 倍, 再定格最后一帧)。"""
    c = dur(src)
    slow = min(max(seconds / c, 1.0), 1.25)
    pad = max(seconds - c * slow, 0)
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setpts={slow:.4f}*PTS,fps=24,"
          f"tpad=stop_mode=clone:stop_duration={pad:.3f}")
    args = ["-i", str(src), "-vf", vf, "-t", f"{seconds:.3f}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "20"]
    if keep_audio and has_audio(src) and slow == 1.0:
        args += ["-c:a", "aac", "-af", f"apad=whole_dur={seconds:.3f}"]
    else:
        args += ["-an"]
    ff(args + [str(dst)], what="统一片段")
    return dst


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> Path:
    path.write_text("\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1)),
                    encoding="utf-8")
    return path


def burn(video: Path, srt: Path, audio: Path | None, out: Path) -> Path:
    font = "Noto Sans CJK SC" if "Sans CJK" in subprocess.run(["fc-list"], capture_output=True, text=True).stdout \
        else "Noto Serif CJK SC"
    style = f"FontName={font},FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=2,MarginV=60"
    args = ["-i", str(video)] + (["-i", str(audio)] if audio else [])
    args += ["-vf", f"subtitles={srt}:force_style='{style}'", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p"]
    args += (["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"] if audio else ["-c:a", "aac"])
    ff(args + [str(out)], timeout=1200, what="烧录字幕")
    return out


def concat(parts: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], what="拼接")
    return out


def asr(base: str, wav: Path) -> str:
    """识别一段音频; 没检测到声音事件 (纯伴奏 / 静音) 返回空串。"""
    body, ctype = _multipart({"model": "sensevoice", "language": "auto", "response_format": "verbose_json"}, {"file": wav})
    req = urllib.request.Request(f"{base}/audio/transcriptions", data=body, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    return d.get("text", "") if (d.get("tags") or d.get("raw_text")) else ""


def voiced(t: str) -> bool:
    """这段有没有人在唱: 识别出 2 个以上的字 (前奏里常见的单个"嗯"不算)。"""
    return len(_cjk(t)) >= 2


def _cjk(t: str) -> str:
    return "".join(ch for ch in t if "\u4e00" <= ch <= "\u9fff" or ch.isalnum())


def _match(lines: list[str], heard: list[str]) -> list[list[int]]:
    """按相似度把歌词行单调地对到各段 (每段最多 2 行); 没对上的段为空。"""
    import difflib
    seg: list[list[int]] = [[] for _ in range(len(heard))]
    nxt = 0
    for k, h in enumerate(heard):
        h = _cjk(h)
        if len(h) < 2:
            continue
        best = []
        for i in range(nxt, len(lines)):
            sc = difflib.SequenceMatcher(None, _cjk(lines[i]), h).ratio()
            if sc >= 0.3:
                best.append(i)
            elif best:
                break
            if len(best) == 2:
                break
        if best:
            seg[k] = best
            nxt = best[-1] + 1
    return seg


def align_lyrics(lines: list[str], heard: list[str]) -> list[str]:
    """字幕用原歌词, 不用识别出来的字 (带伴奏的歌识别错字多, 2026-10-23 实测把"挺直腰"识别成"停只有")。
    heard[k] 是第 k 段识别到的文字, 只用来判断这段唱到了哪几句: 按相似度单调地把歌词行对到段上,
    没对上的行均匀分到前后空着的段里。返回每段要显示的字幕 (最多 2 行)。"""
    seg = _match(lines, heard)
    n = len(heard)
    # 没对上的行: 放进它前后两个已对上段之间的空段, 没有空段就并到前一段
    anchors = [(k, v[0], v[-1]) for k, v in enumerate(seg) if v]
    bounds = [(-1, -1)] + [(k, last) for k, _, last in anchors] + [(n, len(lines))]
    starts = [first for _, first, _ in anchors] + [len(lines)]
    for (ka, la), (kb, _), fb in zip(bounds, bounds[1:], starts):
        todo = list(range(la + 1, fb))
        gap = [k for k in range(ka + 1, kb) if voiced(heard[k])]   # 纯伴奏段不放字幕 (2026-10-23 前 26 秒前奏被配上了主歌)
        if not todo:
            continue
        if gap:   # 均匀摊开: 6 行分 5 段 → 1,1,1,1,2
            for j, k in enumerate(gap):
                seg[k] = todo[round(j * len(todo) / len(gap)):round((j + 1) * len(todo) / len(gap))]
        elif ka >= 0 and kb < n:   # 夹在两段之间又没有空段: 并到前一段; 开头 / 结尾没唱到的行不硬塞
            seg[ka] = seg[ka] + todo
    return ["\n".join(lines[i] for i in v[:2]) for v in seg]


# ── MV ─────────────────────────────────────────────────────────
def energy(wavs: list[Path]) -> list[str]:
    """每段响度 (平均音量 dB), 按整首歌的相对高低分 强 / 中 / 弱。"""
    vols = []
    for w in wavs:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(w), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        vols.append(float(m.group(1)) if m else -60.0)
    if not vols:
        return []
    # 和整首歌的中位响度比 (按最响最轻的差距分档时, 整首都很响的 DJ 曲会几乎全是"强")
    med = sorted(vols)[len(vols) // 2]
    return ["强" if v >= med + 1.5 else "弱" if v <= med - 3 else "中" for v in vols]


MOTION = {   # 只管镜头和特效; 人物保持首帧的姿势只做一个动作 (2026-10-23: "强"写成"动作幅度大", 竖大拇指后又多挥了一次手)
    "强": "镜头快速推近或环绕, 特效强烈有冲击感, 人物保持首帧的手势和姿势, 只做一个连贯动作",
    "中": "镜头平稳移动, 人物保持首帧的手势和姿势, 动作自然",
    "弱": "镜头缓慢推拉, 安静氛围, 人物保持首帧的姿势, 动作很轻"}


def listen(song: Path, d: Path, svc: dict) -> list[str]:
    """把歌切成 H3 段长, 逐段识别 (纯伴奏段为空串)。"""
    d.mkdir(parents=True, exist_ok=True)
    n = math.ceil(dur(song) / SEG - 0.05)
    heard = []
    for k in range(n):
        wav = d / f"{k + 1:02d}.wav"
        if not wav.exists():
            ff(["-ss", f"{k * SEG:.3f}", "-t", f"{SEG:.3f}", "-i", str(song), "-ac", "1", "-ar", "24000", str(wav)],
               what="切歌")
        try:
            heard.append(asr(svc["asr"], wav) if svc["asr"] else "")
        except Exception as e:
            WARN.append(f"第 {k + 1} 段识别失败: {e}")
            heard.append("")
    return heard


def song_score(lines: list[str], heard: list[str]) -> dict:
    """前奏多长 (第一段有人声的位置)、写好的歌词唱到了几成。"""
    first = next((k for k, h in enumerate(heard) if voiced(h)), len(heard))
    got = {i for v in _match(lines, heard) for i in v}
    return {"intro_s": round(first * SEG, 1), "coverage": round(len(got) / max(len(lines), 1), 2),
            "voiced_segments": sum(voiced(h) for h in heard), "segments": len(heard)}


def make_song(plan: dict, out: Path, svc: dict, song: Path) -> None:
    """写歌 + 质检: 前奏超过 8 秒或歌词唱到不足 6 成, 就带着"前奏短、每句都唱"的要求重写一次, 取唱得更全的。
    (2026-10-23 实测: 40 秒慢歌放了 10 句词, 前奏 26 秒、主歌一句没唱)"""
    s = plan.get("song") or {}
    lyrics = s.get("lyrics", "")
    (out / "lyrics.txt").write_text(lyrics, encoding="utf-8")
    lines = [x.strip() for x in lyrics.splitlines() if x.strip() and not x.strip().startswith("[")]
    best = None
    for attempt in (1, 2):
        cand = out / f"song_try{attempt}.mp3"
        prompt = s.get("prompt", "流行, 女声")
        dur_s = int(s.get("duration", 40))
        if attempt == 2:
            prompt += ", 前奏很短, 4 秒内开始唱, 每一句歌词都要唱出来"
            bpm = int(re.search(r"(\d{2,3})\s*BPM", prompt, re.I).group(1)) if re.search(r"(\d{2,3})\s*BPM", prompt, re.I) else 100
            dur_s = max(dur_s, int(len(lines) * (4 if bpm <= 85 else 3)) + 6)   # 慢歌每句约 4 秒
        log(f"[歌] 第 {attempt} 次 ACE-Step 生成 ({dur_s}s)…")
        run([sys.executable, str(SONG), "gen", "--prompt", prompt, "--lyrics-file", str(out / "lyrics.txt"),
             "--duration", str(dur_s), "-o", str(cand)], 1200, "写歌")
        if not lines or not svc["asr"]:
            best = (cand, {})
            break
        sc = song_score(lines, listen(cand, out / f"qa{attempt}", svc))
        log(f"[歌] 第 {attempt} 次质检: 前奏 {sc['intro_s']}s, 歌词唱到 {int(sc['coverage'] * 100)}%")
        if best is None or (sc["coverage"], -sc["intro_s"]) > (best[1]["coverage"], -best[1]["intro_s"]):
            best = (cand, sc)
        if sc["intro_s"] <= 8 and sc["coverage"] >= 0.6:
            break
    if best[1] and (best[1]["intro_s"] > 8 or best[1]["coverage"] < 0.6):
        WARN.append(f"歌曲质检没完全达标 (前奏 {best[1]['intro_s']}s, 歌词唱到 {int(best[1]['coverage'] * 100)}%), 用了两次里更好的一首")
    shutil.copy(best[0], song)
    (out / "song_qa.json").write_text(json.dumps(best[1], ensure_ascii=False), encoding="utf-8")


def assign_shots(plan: dict, heard: list[str], lines: list[str]) -> list[int]:
    """按"这一段实际唱的是哪句"给每段挑镜头 (返回镜头下标)。
    shot["lyric"] 写这个镜头演的歌词; shot["kind"] = intro / instrumental / outro 给没人唱的段。
    镜头都没写 lyric 时退回按顺序。"""
    import difflib
    shots = plan["shots"]
    n = len(heard)
    if not any(s.get("lyric") for s in shots):
        return [k % len(shots) for k in range(n)]
    sung = align_lyrics(lines, heard) if lines else ["" for _ in heard]
    voice = [voiced(h) for h in heard]
    first = next((k for k in range(n) if voice[k]), n)
    last = max((k for k in range(n) if voice[k]), default=-1)
    lyric_shots = [i for i, s in enumerate(shots) if s.get("lyric")]
    by_kind = {kd: [i for i, s in enumerate(shots) if s.get("kind") == kd] for kd in ("intro", "instrumental", "outro")}
    free = [i for i, s in enumerate(shots) if not s.get("lyric") and not s.get("kind")]
    out, used, nxt = [], {}, 0
    matched = [k for k, v in enumerate(_match(lines, heard)) if v] if lines else []
    last_matched = max(matched, default=-1)

    def pick(pool: list[int]) -> int | None:
        if not pool:
            return None
        i = min(pool, key=lambda x: (used.get(x, 0), pool.index(x)))   # 同类里用得最少的
        used[i] = used.get(i, 0) + 1
        return i

    for k in range(n):
        text = _cjk(sung[k] or heard[k])
        cand = None
        if voice[k] and text:
            sc = [(difflib.SequenceMatcher(None, _cjk(str(shots[i]["lyric"])), text).ratio(), i) for i in lyric_shots]
            best = max(sc, default=(0, None))
            if best[0] >= 0.35:
                cand = best[1]
                used[cand] = used.get(cand, 0) + 1
            elif k > last_matched >= 0 and by_kind["outro"]:   # 最后一句唱完之后的尾声: 用尾奏镜头
                cand = pick(by_kind["outro"])
            elif lyric_shots:   # 唱了但没对上: 按顺序拿下一个歌词镜头
                cand = lyric_shots[nxt % len(lyric_shots)]
                nxt += 1
        if cand is None:
            kd = "intro" if k < first else "outro" if k > last else "instrumental"
            for pool in (by_kind[kd], by_kind["instrumental"], free):   # 镜头下标 0 也是有效值, 不能用 or 串
                if pool:
                    cand = pick(pool)
                    break
        if cand is None:
            cand = k % len(shots)
        if out and cand == out[-1] and len(shots) > 1:
            cand = _alt(cand, out, k, shots, lyric_shots, by_kind, free, used, text if voice[k] else "")
        out.append(cand)
    return out


def _alt(cand: int, out: list[int], k: int, shots: list, lyric_shots: list, by_kind: dict, free: list,
         used: dict, text: str) -> int:
    """相邻两段别用同一个镜头 (2026-10-23: 结尾 3 段全是同一个镜头, 看着重复)。
    依次换: 唱词第二相近的镜头 → 同类 (尾奏/间奏/前奏) 镜头 → 没写标签的镜头 → 全片用得最少的镜头。"""
    import difflib
    prev = out[-1]
    pools = []
    if text:
        ranked = sorted((i for i in lyric_shots if i != prev),
                        key=lambda i: -difflib.SequenceMatcher(None, _cjk(str(shots[i]["lyric"])), text).ratio())
        pools.append(ranked[:1])
    kd = shots[cand].get("kind")
    pools += [by_kind.get(kd, []) if kd else [], by_kind.get("outro", []), by_kind.get("instrumental", []), free,
              list(range(len(shots)))]
    for pool in pools:
        opts = [i for i in pool if i != prev]
        if opts:
            pick = min(opts, key=lambda i: (used.get(i, 0) + out.count(i), opts.index(i)))
            used[pick] = used.get(pick, 0) + 1
            return pick
    return cand


def apply_overrides(plan: dict, out: Path) -> None:
    """出片后自修时重写过的提示词 (prompt_overrides.json, 镜头号 → image / motion), 覆盖优化结果。"""
    f = out / "prompt_overrides.json"
    if not f.exists():
        return
    for no, d in json.loads(f.read_text(encoding="utf-8")).items():
        try:
            sh = plan["shots"][int(no) - 1]
        except (ValueError, IndexError):
            continue
        if d.get("image"):
            sh["_img"] = d["image"]
        if d.get("motion"):
            sh["_motion"] = d["motion"]


def seg_review(plan: dict, out: Path, n: int, shot_of: list[int]) -> list[dict]:
    """出片后逐段检查 (每段取中间一帧, 分批交给识图模型): 画面是否符合该段要求、画风统一、乱码字、人物倒挂/崩坏、动作跑偏。
    返回 [{"seg": 段号, "type": "image"|"motion", "why": …}]。"""
    want = "真人写实" if look_of(plan) == "real" else "动漫"
    bad = []
    for b in range(0, n, 6):
        segs = list(range(b + 1, min(b + 7, n + 1)))
        imgs, desc = [], []
        for k in segs:
            v = out / "clips" / f"n{k:02d}.mp4"
            if not v.exists():
                continue
            f = v.parent / f".rv_mid_{k:02d}.jpg"
            ff(["-ss", f"{dur(v) / 2:.2f}", "-i", str(v), "-frames:v", "1", "-vf", "scale=512:-2", str(f)], 60, "抽帧")
            sh = plan["shots"][shot_of[k - 1]]
            imgs.append(f)
            desc.append(f"段{k}: 应该是「{sh.get('image', '')}」, 动作「{sh.get('motion', '')}」")
        if not imgs:
            continue
        j = ask_vision(imgs, f"下面 {len(imgs)} 张依次是一条 MV 各段的中间帧 (画风应统一为{want})。各段要求:\n" + "\n".join(desc) +
                       "\n找出明显有问题的段: 画面和要求不符 / 画风不是" + want + " / 画面里有乱码文字或招牌字 / 人物倒挂、变形、崩坏 / "
                       "动作明显跑偏。小瑕疵别报。type: 画面内容/画风/文字问题写 image, 动作问题写 motion。",
                       '{"bad": [{"seg": 段号, "type": "image 或 motion", "why": "具体问题"}]}')
        for x in (j or {}).get("bad") or []:
            try:
                if int(x.get("seg")) in segs:
                    bad.append({"seg": int(x["seg"]), "type": x.get("type", "image"), "why": str(x.get("why", ""))})
            except (TypeError, ValueError):
                continue
    return bad


def repair(plan: dict, out: Path, bad: list[dict], shot_of: list[int]) -> None:
    """按逐段检查结果自修一轮: 大模型重写出问题镜头的提示词, 删掉它的首帧 (画面问题) 和用到它的所有片段, 下一次运行只重做这些。"""
    f = out / "prompt_overrides.json"
    ov = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    done = set()
    for x in bad:
        si = shot_of[x["seg"] - 1]
        if (si, x["type"]) in done:
            continue
        done.add((si, x["type"]))
        sh = plan["shots"][si]
        d = ov.setdefault(str(si + 1), {})
        if x["type"] == "motion":
            d["motion"] = rewrite_prompt(plan, sh, "视频", sh.get("_motion") or sh.get("motion", ""), x["why"])
        else:
            d["image"] = rewrite_prompt(plan, sh, "生图", sh.get("_img") or sh.get("image", ""), x["why"])
            (out / "frames" / f"{si + 1:02d}.png").unlink(missing_ok=True)
        for k, s2 in enumerate(shot_of, 1):
            if s2 == si:
                for name in (f"{k:02d}.mp4", f"n{k:02d}.mp4"):
                    (out / "clips" / name).unlink(missing_ok=True)
        WARN.append(f"出片自查: 第 {x['seg']} 段 ({x['type']}) {x['why'][:80]} → 已重写镜头 {si + 1} 的提示词并重做")
    f.write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_mv(plan: dict, out: Path) -> dict:
    svc, t_all = services(), time.time()
    song = out / "song.mp3"
    if plan.get("song_file"):
        src = Path(plan["song_file"]).resolve()
        if not src.exists():
            raise RuntimeError(f"song_file 不存在: {src}")
        if src != song.resolve():   # 歌已经放在输出目录里 (song_file 就是 <输出目录>/song.mp3) 时不用复制
            shutil.copy(src, song)
    elif not song.exists():
        make_song(plan, out, svc, song)
    total = dur(song)
    n = math.ceil(total / SEG - 0.05)
    log(f"[歌] {total:.1f}s → {n} 段")
    imgs = frames(plan, out, svc)
    seg_dir = out / "segs"
    heard = listen(song, seg_dir, svc)   # 先听: 每段唱的是什么 / 是不是纯伴奏
    lyric_src = (out / "lyrics.txt").read_text(encoding="utf-8") if (out / "lyrics.txt").exists() else \
        (plan.get("song") or {}).get("lyrics", "")
    lines = [x.strip() for x in lyric_src.splitlines() if x.strip() and not x.strip().startswith("[")]
    shot_of = assign_shots(plan, heard, lines)
    en = energy([seg_dir / f"{k + 1:02d}.wav" for k in range(n)])
    log("[节奏] " + " ".join(f"{k + 1}:{e}" for k, e in enumerate(en)))
    log("[镜头] 按唱词排: " + " ".join(f"{k + 1}→{i + 1}" for k, i in enumerate(shot_of)))
    mf = out / "clips" / "shot_map.json"   # 续跑时: 某段分到的镜头变了 (规则或方案改过), 它的旧视频作废重做
    (out / "clips").mkdir(parents=True, exist_ok=True)
    old_map = json.loads(mf.read_text()) if mf.exists() else {}
    for k, si in enumerate(shot_of, 1):
        if str(k) in old_map and old_map[str(k)] != si:
            for name in (f"{k:02d}.mp4", f"n{k:02d}.mp4"):
                (out / "clips" / name).unlink(missing_ok=True)
            log(f"[镜头] 段 {k} 改用镜头 {si + 1}, 重做")
    mf.write_text(json.dumps({str(k): si for k, si in enumerate(shot_of, 1)}))
    jobs = []
    for k in range(n):
        wav = seg_dir / f"{k + 1:02d}.wav"
        si = shot_of[k]
        shot = plan["shots"][si]
        motion = f"{shot.get('_motion') or shot.get('motion', '人物随音乐摆动')}, {MOTION.get(en[k] if k < len(en) else '中', '')}"
        ref = out / "frames" / "ref.png"
        jobs.append({"idx": k + 1, "image": imgs[si], "audio": wav, "engine": shot.get("engine"),
                     # 有人唱的段用 H3 参考音乐 (r2v, 画面跟唱跟节奏); 纯伴奏段用图生视频, 可分给 AniSora 并行
                     "ref": ref if ref.exists() else None, "cast": has_cast(plan, shot),
                     "fix": (lambda cur, why, _sh=shot: rewrite_prompt(plan, _sh, "视频", cur, why)),
                     "mode": shot.get("mode") or ("r2v" if voiced(heard[k]) else "i2v"),
                     "last": _last_of(plan, si, imgs),
                     "prompt": f"{style_of(plan)}，{motion}",
                     "h3_prompt": f"<Picture 1> 中的画面动起来: {motion}, 镜头和特效随 <Audio 1> 的音乐节奏, 人物保持图中的手势和姿势只做一个动作, 保持{'真人写实画面' if look_of(plan) == 'real' else '动漫画风'}和人物外貌不变"})
    vids = clips(jobs, svc, out, look_of(plan), h3_size(plan))
    parts = [norm(vids[k + 1], out / "clips" / f"n{k + 1:02d}.mp4", min(SEG, total - k * SEG)) for k in range(n)]
    shown = align_lyrics(lines, heard) if lines else \
        [re.sub(r"[。，、！？,.!?\s]+", " ", h).strip() if voiced(h) else "" for h in heard]
    cues = [(k * SEG, min((k + 1) * SEG, total), t) for k, t in enumerate(shown) if t]
    silent = concat(parts, out / "video_silent.mp4")
    srt = write_srt(cues, out / "final.srt")
    final = burn(silent, srt, song, out / "final.mp4")
    chk = self_check(final, total, len(cues), n)
    flag = out / ".repaired"
    if not flag.exists() and not os.environ.get("ANIME_NO_REPAIR"):
        flag.write_text(time.strftime("%H:%M:%S"))
        bad = seg_review(plan, out, n, shot_of)
        log(f"[出片自查] {len(bad)} 段有问题" + (": " + "; ".join(f"段{x['seg']} {x['why'][:40]}" for x in bad) if bad else " ✓"))
        if bad:
            repair(plan, out, bad, shot_of)
            log("[自修] 只重做有问题的镜头…")
            return cmd_mv(plan, out)   # 做好的部分全部复用; .repaired 保证只修一轮
    qa = json.loads((out / "song_qa.json").read_text()) if (out / "song_qa.json").exists() else {}
    return {"mode": "mv", "file": str(final), "srt": str(srt), "seconds": round(dur(final), 2), "segments": n,
            "self_check": chk, "song_qa": qa, "shot_map": {k + 1: i + 1 for k, i in enumerate(shot_of)},
            "engines": {j["idx"]: f"{j.get('used')}:{j.get('mode_used', '')}" for j in jobs}, "wall_s": round(time.time() - t_all),
            "warnings": WARN}


def self_check(final: Path, expect_s: float | None, n_cues: int, n_parts: int) -> dict:
    """成片自查: 时长 (MV 必须和歌对齐, 误差 ≤ 1 帧)、有没有声音、字幕条数、抽 4 帧看整体。不合格写进 warnings。"""
    d = dur(final)
    chk = {"seconds": round(d, 3), "audio": has_audio(final), "subtitles": n_cues}
    if expect_s is not None:
        chk["av_drift_s"] = round(d - expect_s, 3)
        if abs(d - expect_s) > 1 / 24 + 0.05:
            WARN.append(f"自查: 成片 {d:.2f}s 和歌 {expect_s:.2f}s 没对齐 (差 {d - expect_s:+.2f}s)")
    if not chk["audio"]:
        WARN.append("自查: 成片没有音轨")
    if n_cues < max(1, n_parts // 2):
        WARN.append(f"自查: 字幕只有 {n_cues} 条 (共 {n_parts} 段), 可能有段落没识别出歌词")
    shots = clip_shots(final, 4)
    j = ask_vision(shots, "这是一条成片按时间顺序抽的 4 帧。检查: 画风是否统一、主角是否始终是同一个人、字幕是否清楚可读、有没有花屏黑屏。",
                   '{"ok": true/false, "problems": "不合格时写具体问题"}')
    if j is not None:
        chk["vision_ok"] = bool(j.get("ok"))
        if not j.get("ok"):
            WARN.append(f"自查: 成片抽帧不合格: {j.get('problems', '')}")
    log(f"[自查] {json.dumps(chk, ensure_ascii=False)}")
    return chk


# ── 剧情 ───────────────────────────────────────────────────────
def tts(base: str, text: str, voice: str, emotion: str, out: Path) -> Path:
    if out.exists():
        return out
    body: dict = {"input": text, "voice": voice or "voice_01", "response_format": "wav"}
    if emotion:
        body.update({"instructions": emotion, "emo_alpha": 0.7})
    req = urllib.request.Request(f"{base}/audio/speech", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r, open(out, "wb") as f:
        f.write(r.read())
    return out


def cmd_story(plan: dict, out: Path) -> dict:
    svc, t_all = services(), time.time()
    if not svc["tts"]:
        raise RuntimeError("能力接入里没有 IndexTTS 配音卡 (或设 ANIME_TTS_URL)")
    imgs = frames(plan, out, svc)
    vd = out / "voice"
    vd.mkdir(exist_ok=True)
    voices = plan.get("voices") or {}
    lines = []
    for i, shot in enumerate(plan["shots"], 1):
        for k, ln in enumerate(shot.get("lines") or [], 1):
            p = tts(svc["tts"], ln["text"], voices.get(ln.get("who", ""), "voice_01"), ln.get("emotion", ""),
                    vd / f"{i:02d}_{k}.wav")
            lines.append((i, ln, p, dur(p)))
    log(f"[配音] {len(lines)} 句")
    ref = out / "frames" / "ref.png"
    jobs = [{"idx": i, "image": imgs[i - 1], "engine": s.get("engine"), "ref": ref if ref.exists() else None,
             "cast": has_cast(plan, s), "fix": (lambda cur, why, _sh=s: rewrite_prompt(plan, _sh, "视频", cur, why)),
             "mode": s.get("mode") or "i2v", "last": _last_of(plan, i - 1, imgs),
             "prompt": f"{style_of(plan)}，{s.get('_motion') or s.get('motion', '人物自然地动作, 镜头缓慢推近')}"}
            for i, s in enumerate(plan["shots"], 1)]
    vids = clips(jobs, svc, out, look_of(plan), h3_size(plan))
    parts, cues, t = [], [], 0.0
    for i, s in enumerate(plan["shots"], 1):
        mine = [x for x in lines if x[0] == i]
        a_len = sum(x[3] for x in mine) + 0.3 * max(len(mine) - 1, 0)
        seconds = max(SEG, a_len + 0.8)
        v = norm(vids[i], out / "clips" / f"n{i:02d}.mp4", seconds, keep_audio=True)
        # 本镜头音轨: 台词从 0.4s 起依次排开, 盖在片段自带的环境音上 (环境音压到 0.3)
        inputs, flt, at = ["-i", str(v)], [], 0.4
        for k, (_, ln, p, d) in enumerate(mine, 1):
            inputs += ["-i", str(p)]
            flt.append(f"[{k}:a]adelay={int(at * 1000)}|{int(at * 1000)}[d{k}]")
            cues.append((t + at, t + at + d, f"{ln.get('who', '')}：{ln['text']}" if ln.get("who") not in (None, "", "旁白")
                         else ln["text"]))
            at += d + 0.3
        base_a = "[0:a]volume=0.3[bg]" if has_audio(v) else f"anullsrc=r=44100:cl=stereo,atrim=0:{seconds:.3f}[bg]"
        mix = "".join(f"[d{k}]" for k in range(1, len(mine) + 1))
        fc = ";".join([base_a] + flt + [f"[bg]{mix}amix=inputs={len(mine) + 1}:duration=first:normalize=0[a]"])
        sp = out / "clips" / f"s{i:02d}.mp4"
        ff(inputs + ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                     "-ar", "44100", "-t", f"{seconds:.3f}", str(sp)], what="镜头配音")
        parts.append(sp)
        t += seconds
    joined = concat(parts, out / "video_joined.mp4")
    bgm = None
    if plan.get("bgm"):
        b = plan["bgm"]
        bgm = out / "bgm.mp3"
        if not bgm.exists():
            run([sys.executable, str(SONG), "gen", "--prompt", b.get("prompt", "温暖, 钢琴, 纯音乐"), "--instrumental",
                 "--duration", str(int(t) + 2), "-o", str(bgm)], 1200, "配乐")
        mixed = out / "video_bgm.mp4"
        vol = float(b.get("volume", 0.15))
        ff(["-i", str(joined), "-stream_loop", "-1", "-i", str(bgm), "-filter_complex",
            f"[1:a]volume={vol}[b];[0:a][b]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", str(mixed)], what="混配乐")
        joined = mixed
    srt = write_srt(cues, out / "final.srt")
    final = burn(joined, srt, None, out / "final.mp4")
    chk = self_check(final, None, len(cues), len(plan["shots"]))
    return {"mode": "story", "file": str(final), "srt": str(srt), "seconds": round(dur(final), 2), "self_check": chk,
            "shots": len(plan["shots"]), "lines": len(lines), "engines": {j["idx"]: f"{j.get('used')}:{j.get('mode_used', '')}" for j in jobs},
            "wall_s": round(time.time() - t_all), "warnings": WARN}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    lp = sub.add_parser("listen", help="先听歌: 每段时间点 / 有没有人唱 / 唱的是哪句原词 (设计镜头前先看这张表)")
    lp.add_argument("song")
    lp.add_argument("--lyrics", default="", help="歌词文件 (带 [verse] 等标签也行)")
    lp.add_argument("-o", "--out", default="", help="切段临时目录 (默认 歌所在目录/segs)")
    for name in ("mv", "story", "repair"):
        p = sub.add_parser(name)
        p.add_argument("plan")
        p.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        st = check()
        for k, v in st.items():
            log(f"{'✓' if v['ok'] else '✗'} {k:<8} {v['url'] or '(没有接入)'}")
        return 0 if st["image"]["ok"] and (st["h3"]["ok"] or st["anisora"]["ok"]) else 1
    if a.cmd == "listen":
        song = Path(a.song).resolve()
        lines = [x.strip() for x in (Path(a.lyrics).read_text(encoding="utf-8").splitlines() if a.lyrics else [])
                 if x.strip() and not x.strip().startswith("[")]
        heard = listen(song, Path(a.out) if a.out else song.parent / "segs", services())
        shown = align_lyrics(lines, heard) if lines else heard
        total = dur(song)
        log(f"歌长 {total:.1f}s, {len(heard)} 段 (每段 {SEG:.2f}s)")
        seg_d = Path(a.out) if a.out else song.parent / "segs"
        en = energy([seg_d / f"{k + 1:02d}.wav" for k in range(len(heard))])
        log("| 段 | 时间 | 强弱 | 有人唱 | 唱的是 (原歌词) | 识别到的 |")
        log("|---|---|---|---|---|---|")
        for k, h in enumerate(heard):
            log(f"| {k + 1} | {k * SEG:.1f}-{min((k + 1) * SEG, total):.1f}s | {en[k]} | {'是' if voiced(h) else '否 (伴奏)'} | "
                f"{(shown[k] or '').replace(chr(10), ' / ')} | {h.strip()[:40]} |")
        sc = song_score(lines, heard) if lines else {}
        log("RESULT " + json.dumps({"segments": len(heard), "seconds": round(total, 2), **sc}, ensure_ascii=False))
        return 0
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    if not plan.get("shots"):
        sys.exit("ERROR: plan.json 里没有 shots")
    out = Path(a.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    # 任务锁: 同一个输出目录同时只能有一个任务 (2026-10-23 被中断的会话自己续跑, 和新一轮抢同一台 H3)
    lock = out / ".lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().split()[0])
            os.kill(pid, 0)
            log(f"ERROR: 这个目录已经有任务在跑 (进程 {pid}), 不重复启动。等它结束, 或先停掉: kill {pid}")
            return 2
        except (ValueError, ProcessLookupError, PermissionError, IndexError):
            pass   # 旧进程已不在, 锁失效
    lock.write_text(f"{os.getpid()} {time.strftime('%H:%M:%S')}")
    try:
        if a.cmd == "repair":   # 已出片的 MV 再做一轮 "逐段检查 + 自修"
            (out / ".repaired").unlink(missing_ok=True)
        res = cmd_mv(plan, out) if a.cmd in ("mv", "repair") else cmd_story(plan, out)
    except Exception as e:
        log(f"ERROR: {e}")
        return 1
    finally:
        lock.unlink(missing_ok=True)
    (out / "report.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    log("RESULT " + json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

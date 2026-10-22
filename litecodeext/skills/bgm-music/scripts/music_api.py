#!/usr/bin/env python3
"""外部 AI 作曲 API (可选通道): 配了 key 就生成, 没 key 只打印方案, 不报错。

当前接入: MiniMax 音乐生成 POST https://api.minimax.cn/v1/music_generation
  model: music-3.0 (默认) / music-2.6 / *-free; is_instrumental=true 生成纯音乐 BGM; 带 lyrics 生成歌曲
  key 读取顺序: env MINIMAX_API_KEY → LiteCode config.json 的 music.minimax_api_key (没有就不可用)
  计费以 MiniMax 官网为准 (2026-08 起付费接口不再对新用户开放, 免费变体 RPM=3)。

用法:
  python3 music_api.py status
  python3 music_api.py gen --prompt "轻快的电子流行, 适合美食探店短视频, 120bpm" [--lyrics 歌词.txt] -o song.mp3
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests

API = os.environ.get("MINIMAX_MUSIC_URL", "https://api.minimax.cn/v1/music_generation")


def _key():
    if os.environ.get("MINIMAX_API_KEY"):
        return os.environ["MINIMAX_API_KEY"]
    here = Path(__file__).resolve()
    for p in (Path(os.environ.get("LITECODE_CONFIG", "/nonexistent")), here.parents[3] / "config.json",
              here.parents[4] / "config.json", Path("/opt/litecode/config.json")):
        try:
            k = (json.loads(p.read_text(encoding="utf-8")).get("music") or {}).get("minimax_api_key")
            if k:
                return k
        except Exception:
            continue
    return ""


PLAN = {
    "available": False,
    "reason": "未配置 MiniMax key (env MINIMAX_API_KEY 或 config.json music.minimax_api_key)",
    "fallback": "用 bgm_gen.py 本地程序化生成, 或 music_search.py 找 CC 授权曲",
    "options": [
        {"name": "MiniMax music-3.0", "api": API, "note": "纯音乐/带歌词歌曲, 输出 mp3 url 或 hex"},
        {"name": "天工 SkyMusic / Mureka", "note": "有开放平台, 需单独申请 key, 未接"},
        {"name": "自建 YuE / ACE-Step / MusicGen", "note": "开源模型, 需 GPU (8~24GB 显存), 可部署在 5090 机器, 未部署"},
    ],
}


def generate(prompt, lyrics, out, model):
    key = _key()
    if not key:
        return PLAN
    body = {"model": model, "prompt": prompt, "output_format": "url",
            "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"}}
    if lyrics:
        body["lyrics"] = lyrics
    else:
        body["is_instrumental"] = True
    r = requests.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=600)
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    base = d.get("base_resp") or {}
    if r.status_code != 200 or base.get("status_code", 0) != 0:
        return {"ok": False, "status": r.status_code, "error": base.get("status_msg") or r.text[:300]}
    audio = (d.get("data") or {}).get("audio", "")
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    if audio.startswith("http"):
        p.write_bytes(requests.get(audio, timeout=120).content)
    else:
        p.write_bytes(bytes.fromhex(audio))
    return {"ok": True, "output": str(p.resolve()), "model": model,
            "license": "AI 生成, 版权/商用条款以 MiniMax 用户协议为准; 发布时按规定标注 AI 生成"}


def main():
    ap = argparse.ArgumentParser(description="外部 AI 作曲 API")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    g = sub.add_parser("gen")
    g.add_argument("--prompt", required=True)
    g.add_argument("--lyrics", default="", help="歌词文件路径 (不给 = 纯音乐)")
    g.add_argument("--model", default="music-3.0")
    g.add_argument("-o", "--out", default="/tmp/litecode_workspace/music/ai_song.mp3")
    a = ap.parse_args()
    if a.cmd == "status":
        print(json.dumps({"available": bool(_key()), "api": API} if _key() else PLAN, ensure_ascii=False, indent=2))
        return 0
    lyrics = Path(a.lyrics).read_text(encoding="utf-8") if a.lyrics else ""
    print(json.dumps(generate(a.prompt, lyrics, a.out, a.model), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

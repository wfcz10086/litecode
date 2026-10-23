#!/usr/bin/env python3
"""song.py — AI 写歌 (带人声 + 歌词): 调「能力接入」里的音乐卡 (自建 ACE-Step 1.5), 提交 → 轮询 → 下载, 可选用语音识别核对唱词。

只用标准库。服务地址按顺序找: --base > 环境变量 ACESTEP_URL > config.json 里含 /release_task 的音乐卡 (默认卡优先)。

用法:
  song.py gen --prompt "失恋但很嗨的电子舞曲, EDM, 128 BPM, 女声" --lyrics-file 歌词.txt --duration 90 -o 歌.mp3 --verify
  song.py gen --prompt "钢琴, 温暖, 纯音乐" --instrumental --duration 60 -o bgm.mp3
  song.py status                     # 看服务是否在线 + 用的是哪张卡
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

CFG_PATHS = [os.environ.get("LITECODE_CONFIG", ""), "/opt/litecode/config.json",
             str(Path(__file__).resolve().parents[3] / "config.json")]


def _cfg() -> dict:
    for p in CFG_PATHS:
        if p and Path(p).is_file():
            try:
                return json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _cards(kind: str) -> list[dict]:
    ps = [p for p in (_cfg().get("providers") or []) if isinstance(p, dict)
          and p.get("kind") == kind and p.get("enabled", True)]
    return sorted(ps, key=lambda p: not p.get("default"))


def find_base(explicit: str = "") -> tuple[str, str]:
    """(服务地址, 卡片名)"""
    if explicit:
        return explicit.rstrip("/"), "--base"
    if os.environ.get("ACESTEP_URL"):
        return os.environ["ACESTEP_URL"].rstrip("/"), "ACESTEP_URL"
    for p in _cards("music"):
        m = re.search(r"(https?://[^\s'\"]+?)/release_task", p.get("usage") or "")
        if m:
            return m.group(1), p.get("name", "")
    sys.exit("ERROR: 能力接入里没有可用的 AI 作曲卡 (含 /release_task 的音乐卡)。"
             "请在左上角「能力接入 → 音乐」接入 ACE-Step, 或用 --base 指定地址。")


def find_asr() -> str:
    """OpenAI 兼容语音识别地址 (…/v1); 没有返回空串。"""
    for p in _cards("asr"):
        m = re.search(r"(https?://[^\s'\"]+?)/v1/audio/transcriptions", p.get("usage") or "")
        if m:
            return m.group(1) + "/v1"
        if p.get("base_url"):
            return p["base_url"].rstrip("/")
    return ""


def _post(url: str, body: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def generate(base: str, a) -> Path:
    lyrics = a.lyrics or (Path(a.lyrics_file).read_text(encoding="utf-8") if a.lyrics_file else "")
    if not a.instrumental and not lyrics.strip():
        sys.exit("ERROR: 要人声就得给歌词 (--lyrics / --lyrics-file, 用 [verse] [chorus] [bridge] 分段); 纯音乐加 --instrumental")
    body = {"prompt": a.prompt, "lyrics": "" if a.instrumental else lyrics, "audio_duration": a.duration,
            "vocal_language": a.lang, "thinking": True, "audio_format": Path(a.out).suffix.lstrip(".") or "mp3"}
    if a.bpm:
        body["bpm"] = a.bpm
    if a.seed is not None:
        body["seed"] = a.seed
    t0 = time.time()
    # 任务"丢失"判定: ACE-Step 对不存在的 task_id 也回 status=0 (2026-10-23 实测一首 280 秒的歌提交后
    # 再没开始生成, 调用方一直死等)。正常 1 秒音频约 0.5~1 秒算力, 超过 max(180, 时长×3) 秒仍是 0 就重提一次。
    lost_after = max(180, a.duration * 3)
    item, tid = {}, ""
    for attempt in (1, 2):
        r = _post(f"{base}/release_task", body)
        tid = (r.get("data") or {}).get("task_id")
        if not tid:
            sys.exit(f"ERROR: 提交失败: {json.dumps(r, ensure_ascii=False)[:300]}")
        print(f"[song] 已提交 task_id={tid} (第 {attempt} 次), 生成中…", flush=True)
        ts = time.time()
        while True:
            if time.time() - t0 > a.timeout:
                sys.exit(f"ERROR: 等了 {a.timeout}s 还没生成完 (task_id={tid})")
            time.sleep(3)
            q = _post(f"{base}/query_result", {"task_id_list": [tid]})
            item = (q.get("data") or [{}])[0]
            st = item.get("status")
            if st in (1, 2) or time.time() - ts > lost_after:
                break
        if st == 1:
            break
        if st == 2:
            sys.exit(f"ERROR: 生成失败: {json.dumps(item, ensure_ascii=False)[:400]}")
        print(f"[song] {lost_after}s 仍未开始/完成, 服务端可能丢了这个任务, 重新提交", flush=True)
    else:
        sys.exit(f"ERROR: 连续两次提交都没生成出来 (最后 task_id={tid}); 用 `song.py status` 看服务, 或缩短时长重试")
    res = json.loads(item["result"]) if isinstance(item.get("result"), str) else item.get("result")
    file_url = base + res[0]["file"]
    out = Path(a.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(file_url, timeout=300) as r, open(out, "wb") as f:
        f.write(r.read())
    print(f"[song] 完成 {time.time() - t0:.1f}s → {out}", flush=True)
    return out


def verify(out: Path, asr: str, seconds: int = 60) -> str:
    """把前 N 秒转成文字, 用来核对是不是真在唱歌词 (识别会有个别错字, 只看大体对得上)。"""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "v.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-t", str(seconds), "-ac", "1", "-ar", "16000",
                        str(wav)], check=True)
        bnd = uuid.uuid4().hex
        parts = []
        for k, v in (("model", "sensevoice"), ("language", "auto"), ("response_format", "json")):
            parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="file"; filename="v.wav"\r\n'
                     f'Content-Type: audio/wav\r\n\r\n'.encode() + wav.read_bytes() + b"\r\n")
        parts.append(f"--{bnd}--\r\n".encode())
        req = urllib.request.Request(f"{asr}/audio/transcriptions", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode()).get("text", "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--prompt", required=True, help="风格描述: 曲风, 情绪, 乐器, 速度, 男声/女声")
    g.add_argument("--lyrics", default="")
    g.add_argument("--lyrics-file", default="")
    g.add_argument("--instrumental", action="store_true", help="纯音乐 (不唱)")
    g.add_argument("--duration", type=int, default=90, help="秒, 10~600")
    g.add_argument("--bpm", type=int)
    g.add_argument("--lang", default="zh", help="演唱语言 zh/en/ja/ko/yue…")
    g.add_argument("--seed", type=int)
    g.add_argument("--base", default="", help="服务地址 (默认用能力接入里的音乐卡)")
    g.add_argument("--timeout", type=int, default=900)
    g.add_argument("--verify", action="store_true", help="生成后用能力接入里的语音识别核对唱词")
    g.add_argument("-o", "--out", required=True)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    base, card = find_base(getattr(a, "base", ""))
    if a.cmd == "status":
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=10) as r:
                print(f"OK {card}: {base} → {r.read().decode()[:200]}")
        except Exception as e:
            print(f"ERROR {card}: {base} 连不上: {e}")
            return 1
        print(f"语音识别核对: {find_asr() or '(没有接入, --verify 不可用)'}")
        return 0
    out = generate(base, a)
    info = {"file": str(out), "card": card}
    if a.verify and not a.instrumental:
        asr = find_asr()
        if asr:
            try:
                info["heard"] = verify(out, asr)
                print(f"[song] 识别到的唱词 (前 60 秒): {info['heard']}", flush=True)
            except Exception as e:
                info["heard_error"] = str(e)[:200]
        else:
            info["heard_error"] = "没有接入语音识别, 跳过核对"
    print("RESULT " + json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

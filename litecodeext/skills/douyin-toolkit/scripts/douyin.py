#!/usr/bin/env python3
"""抖音分享文本 / 短链 → 作品信息 + 无水印视频 / 图集原图。

用法 (容器内, SK=/opt/litecode/skills/douyin-toolkit):
  python3 $SK/scripts/douyin.py info  "<分享文本或链接>"            # 只看信息, 打印 JSON
  python3 $SK/scripts/douyin.py download "<分享文本或链接>" [-o 目录]  # 下载无水印视频 / 图集原图

规则:
- 只处理用户给的单条公开分享内容; 不批量、不登录、不绕付费。
- 作品被作者/平台设为「不可下载」(video_control.allow_download=false, 例如 AI 生成内容、隔离内容) 时
  不保存视频 (退出码 3, 打印原因)。只解读文案用 analyze.py, 它会临时取流分析后删除视频。
- 服务器在境外时分享页 (iesdouyin) 会显示「抱歉出错了」, 本脚本优先走 feed 接口, 境外可用;
  仍失败可设代理 DOUYIN_PROXY=http://user:pass@host:port (境内出口) 再试。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
WORKSPACE = Path(os.environ.get("LITECODE_WORKSPACE", "/tmp/litecode_workspace"))
URL_RE = re.compile(r"https?://[A-Za-z0-9./?=&_%~:#+-]+")
ID_PATTERNS = [r"/(?:video|note|slides|share/video|share/note|share/slides)/(\d{15,22})",
               r"modal_id=(\d{15,22})", r"aweme_id=(\d{15,22})", r"item_ids?=(\d{15,22})"]


class DouyinError(RuntimeError):
    pass


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    proxy = os.environ.get("DOUYIN_PROXY")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def extract_url(text: str) -> str:
    """从整段分享口令里抠出链接; 纯数字当作品 id。"""
    text = (text or "").strip()
    if re.fullmatch(r"\d{15,22}", text):
        return text
    urls = URL_RE.findall(text)
    for u in urls:
        if "douyin.com" in u or "iesdouyin.com" in u:
            u = u.rstrip("/.,，。")
            return u + "/" if "v.douyin.com" in u else u
    if urls:
        return urls[0]
    raise DouyinError("没在文本里找到抖音链接 (v.douyin.com / douyin.com / iesdouyin.com)")


def resolve_id(text: str, s=None) -> str:
    s = s or _session()
    url = extract_url(text)
    if url.isdigit():
        return url
    for pat in ID_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # 短链: 逐跳跟随 Location, 不下载页面正文
    cur = url
    for _ in range(6):
        r = s.get(cur, allow_redirects=False, timeout=15)
        loc = r.headers.get("Location") or ""
        for pat in ID_PATTERNS:
            m = re.search(pat, loc or cur)
            if m:
                return m.group(1)
        if not loc:
            break
        cur = loc
    raise DouyinError(f"短链解析不到作品 id (最后地址: {cur[:120]})")


def _fetch_feed(aweme_id: str, s) -> dict:
    """aweme.snssdk.com feed 接口: 第一条就是目标作品 (境外 IP 也能用)。"""
    r = s.get("https://aweme.snssdk.com/aweme/v1/feed/", params={"aweme_id": aweme_id}, timeout=20)
    r.raise_for_status()
    for it in (r.json().get("aweme_list") or []):
        if str(it.get("aweme_id")) == aweme_id:
            return it
    return {}


def _fetch_share_page(aweme_id: str, s) -> dict:
    """iesdouyin 分享页 SSR 数据 (境内出口时可用, 作为备用)。"""
    r = s.get(f"https://www.iesdouyin.com/share/video/{aweme_id}/", timeout=20)
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>", r.text, re.S)
    if not m:
        return {}
    data = json.loads(m.group(1)).get("loaderData", {})
    for v in data.values():
        if isinstance(v, dict) and v.get("videoInfoRes"):
            items = v["videoInfoRes"].get("item_list") or []
            if items:
                return items[0]
    return {}


def fetch_item(aweme_id: str, s=None) -> dict:
    s = s or _session()
    errors = []
    for fn in (_fetch_feed, _fetch_share_page):
        for attempt in range(2):
            try:
                it = fn(aweme_id, s)
                if it:
                    return it
                break
            except Exception as e:  # 网络抖动重试一次
                errors.append(f"{fn.__name__}: {e}")
                time.sleep(1.5)
    raise DouyinError("取不到作品信息 (可能已删除/仅自己可见/地区限制; 境外服务器可设 DOUYIN_PROXY 走境内代理)"
                      + (f" | {errors[-1]}" if errors else ""))


def _urls(addr) -> list:
    return list((addr or {}).get("url_list") or [])


def _best_video_urls(video: dict) -> tuple:
    """按清晰度挑无水印地址: bit_rate 里分辨率最高者 (同分辨率优先 h264, 兼容预览)。"""
    cands = []
    for b in video.get("bit_rate") or []:
        pa = b.get("play_addr") or {}
        h = pa.get("height") or 0
        w = pa.get("width") or 0
        cands.append(((w * h, 0 if b.get("is_h265") else 1, b.get("bit_rate") or 0), _urls(pa),
                      {"gear": b.get("gear_name"), "width": w, "height": h, "h265": bool(b.get("is_h265"))}))
    cands.sort(key=lambda c: c[0], reverse=True)
    urls, info = [], {}
    for _, us, inf in cands:
        if us and not urls:
            info = inf
        urls += us
    pa = video.get("play_addr") or {}
    urls += _urls(pa)
    uri = pa.get("uri")
    if uri and not uri.startswith("http"):
        urls.append(f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0")
    seen, out = set(), []
    for u in urls:  # 去重 + 排除带水印的 playwm
        if u not in seen and "playwm" not in u:
            seen.add(u)
            out.append(u)
    return out, info


def normalize(it: dict) -> dict:
    v = it.get("video") or {}
    vc = it.get("video_control") or {}
    images = []
    for im in it.get("images") or []:
        urls = _urls(im)
        urls.sort(key=lambda u: (".webp" in u, "water" in u))  # 优先 jpeg, 排除水印图
        live = _urls((im.get("video") or {}).get("play_addr"))
        images.append({"urls": urls, "width": im.get("width"), "height": im.get("height"),
                       "live_video": live[0] if live else ""})
    is_images = bool(images)
    video_urls, vinfo = ([], {}) if is_images else _best_video_urls(v)
    fail = ((vc.get("download_info") or {}).get("fail_info") or {})
    st = it.get("statistics") or {}
    music = it.get("music") or {}
    return {
        "id": str(it.get("aweme_id")),
        "type": "images" if is_images else "video",
        "desc": it.get("desc") or it.get("caption") or "",
        "author": (it.get("author") or {}).get("nickname", ""),
        "create_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("create_time") or 0)),
        "duration_s": round((v.get("duration") or 0) / 1000, 2),
        "video": vinfo,
        "video_urls": video_urls,
        "watermark_url": (_urls(v.get("download_addr")) or [""])[0],
        "cover": (_urls(v.get("origin_cover")) or _urls(v.get("cover")) or [""])[0],
        "images": images,
        "music": {"title": music.get("title", ""), "author": music.get("author", ""),
                  "url": (_urls(music.get("play_url")) or [""])[0]},
        "stats": {k: st.get(k) for k in ("digg_count", "comment_count", "collect_count", "share_count")},
        "hashtags": [t.get("hashtag_name") for t in (it.get("text_extra") or []) if t.get("hashtag_name")],
        "allow_download": vc.get("allow_download", True) is not False,
        "download_block_reason": fail.get("msg", ""),
        "aigc": fail.get("reason") == "aigc_forbidden" or bool(it.get("aigc_info", {}).get("aigc_label_type")),
        "share_url": f"https://www.douyin.com/video/{it.get('aweme_id')}",
    }


def get_info(text: str) -> dict:
    s = _session()
    aid = resolve_id(text, s)
    return normalize(fetch_item(aid, s))


def _download(url: str, dest: Path, s, expect: str) -> int:
    with s.get(url, stream=True, timeout=60, headers={"Referer": "https://www.douyin.com/"}) as r:
        ct = r.headers.get("Content-Type", "")
        if r.status_code != 200 or (expect not in ct and "octet-stream" not in ct):
            raise DouyinError(f"HTTP {r.status_code} {ct}")
        n = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                n += len(chunk)
    if n < 1024:
        raise DouyinError(f"文件过小 ({n} B)")
    return n


def probe(path: Path) -> dict:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration:stream=codec_type,codec_name,width,height",
                              "-of", "json", str(path)], capture_output=True, text=True, timeout=30).stdout
        d = json.loads(out or "{}")
    except Exception:
        return {}
    res = {"duration_s": round(float(d.get("format", {}).get("duration") or 0), 2)}
    for st in d.get("streams", []):
        if st.get("codec_type") == "video":
            res.update(video_codec=st.get("codec_name"), width=st.get("width"), height=st.get("height"))
        elif st.get("codec_type") == "audio":
            res["audio_codec"] = st.get("codec_name")
    return res


def fetch_video(info: dict, dest: Path, s=None) -> dict:
    """把无水印视频流写到 dest (不检查 allow_download, 调用方负责)。"""
    s = s or _session()
    last = ""
    for u in info["video_urls"]:
        try:
            size = _download(u, dest, s, "video")
            return {"path": str(dest), "size": size, **probe(dest)}
        except Exception as e:
            last = str(e)
    raise DouyinError(f"所有无水印地址都下载失败: {last}")


def download(text: str, out_dir: str = "", with_cover: bool = True) -> dict:
    s = _session()
    info = normalize(fetch_item(resolve_id(text, s), s))
    d = Path(out_dir) if out_dir else WORKSPACE / "douyin" / info["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"id": info["id"], "type": info["type"], "desc": info["desc"], "author": info["author"],
              "dir": str(d), "files": []}
    if not info["allow_download"]:
        result.update(ok=False, blocked=True, reason=info["download_block_reason"] or "作者/平台关闭了下载")
        return result
    if info["type"] == "video":
        r = fetch_video(info, d / f"douyin_{info['id']}.mp4", s)
        r["watermark_free"] = True
        result["files"].append(r)
    else:
        for i, im in enumerate(info["images"], 1):
            for u in im["urls"]:
                ext = ".webp" if ".webp" in u.split("?")[0] else ".jpg"
                try:
                    size = _download(u, d / f"img_{i:02d}{ext}", s, "image")
                    result["files"].append({"path": str(d / f"img_{i:02d}{ext}"), "size": size})
                    break
                except Exception:
                    continue
            if im["live_video"]:
                try:
                    p = d / f"img_{i:02d}_live.mp4"
                    result["files"].append({"path": str(p), "size": _download(im["live_video"], p, s, "video")})
                except Exception:
                    pass
    if with_cover and info["cover"]:
        try:
            _download(info["cover"], d / "cover.jpg", s, "image")
            result["cover"] = str(d / "cover.jpg")
        except Exception:
            pass
    result["ok"] = bool(result["files"])
    return result


def main():
    ap = argparse.ArgumentParser(description="抖音分享链接解析 / 无水印下载")
    ap.add_argument("cmd", choices=["info", "download"])
    ap.add_argument("text", help="分享口令全文 / 链接 / 作品 id")
    ap.add_argument("-o", "--out", default="", help="输出目录 (默认 /tmp/litecode_workspace/douyin/<id>)")
    ap.add_argument("--no-cover", action="store_true")
    a = ap.parse_args()
    try:
        if a.cmd == "info":
            info = get_info(a.text)
            info["video_urls"] = info["video_urls"][:2]
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        r = download(a.text, a.out, not a.no_cover)
    except DouyinError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("blocked"):
        print(f"\n[不可下载] {r['reason']} —— 已尊重作者/平台设置, 未保存视频。"
              f"如只需文案解读: python3 {Path(__file__).parent}/analyze.py \"<同一链接>\"", file=sys.stderr)
        return 3
    for f in r["files"]:
        extra = f" 时长 {f.get('duration_s')}s {f.get('width')}x{f.get('height')}" if f.get("duration_s") else ""
        print(f"已保存: {f['path']} ({f['size'] / 1e6:.2f} MB){extra}", file=sys.stderr)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

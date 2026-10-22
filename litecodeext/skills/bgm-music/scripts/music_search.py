#!/usr/bin/env python3
"""免版权 / CC 授权音乐检索 + 下载 (带授权记录)。

来源 (2026-10 在本机容器实测可访问):
  ccmixter   ccmixter.org 开放 API, 每首带 CC 协议; 默认只要可商用的 by / by-sa / cc0 (署名即可)
  archive    archive.org netlabels (独立厂牌音乐) 合集, 按 licenseurl 过滤
  (Pixabay Music 在本机网络 403, FMA 无公开 API, 未接)

用法:
  python3 music_search.py search --tags ambient,piano [--src ccmixter|archive] [--min 60 --max 240] [--limit 8] [--nc]
  python3 music_search.py download <search 结果里的 id> [-o 目录]
    id 形如 ccmixter:14476 / archive:<identifier>
下载后同目录写 <文件名>.LICENSE.txt (曲名/作者/协议/原始页面/署名文案), 发布时把署名文案贴进简介。
--nc 才会包含 NC (禁止商用) 协议的曲子, 只能用于非商业场景。
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 LiteCode-bgm/1.0"}
WORKSPACE = Path("/tmp/litecode_workspace/music")
COMMERCIAL = ("by/", "by-sa/", "zero/", "publicdomain", "cc0")


def _mmss(s):
    if not s:
        return 0
    parts = [int(p) for p in re.findall(r"\d+", str(s))]
    return parts[0] * 60 + parts[1] if len(parts) == 2 else (parts[0] if parts else 0)


def search_ccmixter(tags, limit, dmin, dmax, nc):
    lic = [] if nc else ["by", "by-sa", "cc0"]
    out = []
    for lc in (lic or [""]):
        params = {"f": "json", "tags": ",".join(tags), "limit": limit * 3, "sort": "rank"}
        if lc:
            params["lic"] = lc
        try:
            rows = requests.get("http://ccmixter.org/api/query", params=params, headers=UA, timeout=20).json()
        except Exception:
            continue
        for r in rows:
            f = next((x for x in r.get("files", []) if x.get("download_url", "").endswith(".mp3")), None)
            if not f:
                continue
            dur = _mmss((f.get("file_format_info") or {}).get("ps"))
            if (dmin and dur and dur < dmin) or (dmax and dur and dur > dmax):
                continue
            out.append({"id": f"ccmixter:{r['upload_id']}", "title": r.get("upload_name"),
                        "artist": r.get("user_real_name") or r.get("user_name"), "duration_s": dur,
                        "license": r.get("license_name"), "license_url": r.get("license_url"),
                        "page": r.get("file_page_url"), "url": f["download_url"],
                        "tags": (r.get("upload_extra") or {}).get("usertags", "")})
    seen, res = set(), []
    for x in out:
        if x["id"] not in seen:
            seen.add(x["id"])
            res.append(x)
    return res[:limit]


def search_archive(tags, limit, dmin, dmax, nc):
    q = "collection:(netlabels) AND mediatype:(audio) AND licenseurl:* AND (" + \
        " OR ".join(f"subject:({t})" for t in tags) + ")"
    params = {"q": q, "fl[]": ["identifier", "title", "creator", "licenseurl"], "rows": limit * (3 if nc else 25),
              "output": "json", "sort[]": "downloads desc"}
    try:
        docs = requests.get("https://archive.org/advancedsearch.php", params=params, headers=UA,
                            timeout=20).json()["response"]["docs"]
    except Exception:
        return []
    res = []
    for d in docs:
        lic = d.get("licenseurl", "")
        if not nc and ("-nc" in lic or not any(c in lic for c in COMMERCIAL)):
            continue
        res.append({"id": f"archive:{d['identifier']}", "title": d.get("title"), "artist": d.get("creator"),
                    "license": lic.rstrip("/").split("/licenses/")[-1] if "/licenses/" in lic else lic,
                    "license_url": lic, "page": f"https://archive.org/details/{d['identifier']}", "url": ""})
        if len(res) >= limit:
            break
    return res


def _archive_file(ident):
    meta = requests.get(f"https://archive.org/metadata/{ident}", headers=UA, timeout=20).json()
    files = [f for f in meta.get("files", []) if f.get("name", "").lower().endswith(".mp3")]
    files.sort(key=lambda f: f["name"])   # 专辑按曲目序, 取第 1 首 (要别的曲目去来源页面挑)
    if not files:
        raise SystemExit("该条目没有 mp3 文件")
    f = files[0]
    md = meta.get("metadata", {})
    return {"title": md.get("title"), "artist": md.get("creator") or ident, "license_url": md.get("licenseurl", ""),
            "page": f"https://archive.org/details/{ident}",
            "url": f"https://archive.org/download/{ident}/{requests.utils.quote(f['name'])}"}


def download(item_id, out_dir):
    src, _, key = item_id.partition(":")
    if src == "ccmixter":
        r = requests.get("http://ccmixter.org/api/query", params={"f": "json", "ids": key}, headers=UA, timeout=20).json()
        if not r:
            raise SystemExit("ccmixter 查不到该 id")
        r = r[0]
        f = next(x for x in r["files"] if x.get("download_url", "").endswith(".mp3"))
        info = {"title": r["upload_name"], "artist": r.get("user_real_name") or r.get("user_name"),
                "license": r.get("license_name"), "license_url": r.get("license_url"),
                "page": r.get("file_page_url"), "url": f["download_url"]}
    elif src == "archive":
        info = _archive_file(key)
        info["license"] = info["license_url"]
    else:
        raise SystemExit("id 需形如 ccmixter:123 或 archive:<identifier>")
    out = Path(out_dir) if out_dir else WORKSPACE
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w\-一-鿿]+", "_", f"{info['artist']}_{info['title']}")[:80] + ".mp3"
    p = out / name
    with requests.get(info["url"], headers={**UA, "Referer": info["page"] or ""}, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(p, "wb") as fh:
            for c in resp.iter_content(1 << 16):
                fh.write(c)
    credit = f"Music: \"{info['title']}\" by {info['artist']} ({info['page']}), licensed under {info['license_url']}"
    lic_txt = (f"曲名: {info['title']}\n作者: {info['artist']}\n协议: {info['license']} {info['license_url']}\n"
               f"来源页面: {info['page']}\n下载地址: {info['url']}\n署名文案 (发布时贴进简介):\n{credit}\n"
               "注意: NC 协议禁止商用; SA 协议要求衍生作品同协议发布; 以来源页面为准。\n")
    lp = p.with_suffix(".LICENSE.txt")
    lp.write_text(lic_txt, encoding="utf-8")
    return {"ok": True, "output": str(p.resolve()), "license_file": str(lp.resolve()), "size": p.stat().st_size,
            **info, "credit": credit}


def main():
    ap = argparse.ArgumentParser(description="CC 授权音乐检索下载")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--tags", required=True, help="英文标签, 逗号分隔: ambient,piano,electronic,cinematic,happy...")
    s.add_argument("--src", default="ccmixter", choices=["ccmixter", "archive", "all"])
    s.add_argument("--min", type=int, default=0)
    s.add_argument("--max", type=int, default=0)
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--nc", action="store_true")
    d = sub.add_parser("download")
    d.add_argument("id")
    d.add_argument("-o", "--out", default="")
    a = ap.parse_args()
    if a.cmd == "search":
        tags = [t.strip() for t in a.tags.split(",") if t.strip()]
        res = []
        if a.src in ("ccmixter", "all"):
            res += search_ccmixter(tags, a.limit, a.min, a.max, a.nc)
        if a.src in ("archive", "all"):
            res += search_archive(tags, a.limit, a.min, a.max, a.nc)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res else 1
    print(json.dumps(download(a.id, a.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

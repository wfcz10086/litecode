#!/usr/bin/env python3
"""
run_auto_web_test.py — P34-d-10 自适应 Web UI 自动测试 driver (v2.0 #6)
======================================================================

流程:
1. browser_login 真登录 LiteCode WebUI
2. deep_inspect 列出 ≥ 30 个 interactive 元素
3. 按元素分组生成 ≥ 30 个测试用例
   - 顶栏按钮 (sbt): 点击 + 截图 + vision 判断面板是否打开
   - 输入框 (input/textarea): placeholder + visibility 检查 (不真填, 防破数据)
   - select: 选项数检查 (不切换防破当前 active 状态)
   - link: href 存在 + 标签合法
   - 高危按钮 (delete/重置/清空): 只验存在, 不点
4. 真跑每个 case → before/after 截图 → vision_check
5. 输出 reports/auto_web_test/<ts>/ {elements.json, cases.json,
   results.json, index.html, snap_*.png, vision_*.json}

用法:
  python3 tests/run_auto_web_test.py --server http://127.0.0.1:18789 \
      --web http://127.0.0.1:18790 --password CHANGE_ME_PASSWORD

  --skip-vision   不跑 vision_check (debug/快速)
  --max-cases N   限制用例数 (debug, 默认全跑)
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports" / "auto_web_test"

# 高危按钮关键词 — 只验存在不真点
_HIGH_RISK = ("删除", "重置", "清空", "格式化", "delete", "reset",
              "clear", "destroy", "drop", "wipe", "退出")


def _post_act(server: str, action: str, params: dict,
              session: str = "auto_test", shot: bool = False,
              wait_ms: int = 500, timeout: int = 12) -> dict:
    """POST /act. 默认 timeout 12s 防卡死 (单 case)."""
    body = {"action": action, "params": params, "session": session,
            "shot": shot, "wait": wait_ms}
    req = urllib.request.Request(
        f"{server}/act", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"ok": False, "msg": f"call failed: {type(e).__name__}: {e}", "data": None}


def _save_b64_png(b64: str, path: Path) -> bool:
    if not b64:
        return False
    try:
        path.write_bytes(base64.b64decode(b64))
        return True
    except (ValueError, OSError):
        return False


def step_login(browser_url: str, web_url: str, password: str) -> dict:
    """1. 真登录: goto → 检查是否已登录 → 若否则 fill #lpwd + 点登录"""
    print("[step] login → " + web_url)
    r1 = _post_act(browser_url, "goto", {"url": web_url}, wait_ms=1500)
    if not r1.get("ok"):
        return {"ok": False, "msg": f"goto 失败: {r1.get('msg')}"}

    # 检测是否已在主 UI: #ls (登录层) 不可见即已登录
    r_check = _post_act(browser_url, "eval",
                        {"script": "(function(){const el=document.getElementById('ls');"
                                   "if(!el) return 'no_login_layer';"
                                   "const s=getComputedStyle(el);"
                                   "return (s.display==='none'||s.visibility==='hidden')"
                                   "?'already_logged_in':'need_login';})()"})
    state = r_check.get("data", "")
    if state == "already_logged_in" or state == "no_login_layer":
        return {"ok": True, "msg": f"已登录 ({state}), 跳过 fill+click",
                "skipped": True}

    r2 = _post_act(browser_url, "extract_form", {})
    fields = (r2.get("data") or {}).get("fields") or []
    pwd_sel = None
    for f in fields:
        if (f.get("type") or "").lower() == "password" and f.get("id"):
            pwd_sel = f"#{f['id']}"
            break
    if not pwd_sel:
        return {"ok": False, "msg": "未找到 password 字段"}
    _post_act(browser_url, "fill_form", {"fields": {pwd_sel: password}})
    r4 = _post_act(browser_url, "click", {"sel": "button.lb_"}, wait_ms=2500)
    if not r4.get("ok"):
        r4 = _post_act(browser_url, "click_text", {"text": "登录"}, wait_ms=2500)
    if not r4.get("ok"):
        return {"ok": False, "msg": f"click 登录 失败: {r4.get('msg')}"}
    time.sleep(1.0)
    return {"ok": True, "msg": "登录成功"}


def step_inspect_elements(browser_url: str) -> dict:
    """2. deep_inspect 列出所有 interactive 元素"""
    print("[step] deep_inspect body")
    r = _post_act(browser_url, "deep_inspect", {"sel": "body", "max": 1000},
                  wait_ms=300, timeout=15)
    if not r.get("ok"):
        return {"ok": False, "msg": r.get("msg"), "elements": []}
    d = r.get("data") or {}
    return {"ok": True, "total": d.get("total", 0),
            "groups": d.get("groups", {})}


def step_generate_cases(elements: dict) -> list:
    """3. 按元素分组生成测试用例 — selector 去重 + 高危 skip 真点"""
    cases: list[dict[str, Any]] = []
    cid = 0
    groups = elements.get("groups", {})
    seen_sels: set = set()  # selector 去重 (#id 唯一, tag.cls 多 elem 只测一次)

    # button: visible 的真点; 隐藏的只验存在 (避免点不到 + 报错)
    for el in groups.get("button", []):
        if el["sel"] in seen_sels:
            continue
        seen_sels.add(el["sel"])
        cid += 1
        text = el.get("text", "").strip()
        risky = any(k in text.lower() for k in _HIGH_RISK)
        vis = el.get("visible")
        if not vis or risky:
            action = "exists_only"
            expect = "按钮 DOM 存在" if not vis else "高危按钮存在(不真点)"
        else:
            action = "click"
            expect = "点击后界面有变化(面板打开/导航跳转/按钮反馈)"
        cases.append({
            "id": f"c{cid:03d}", "type": "button",
            "selector": el["sel"], "label": text or el.get("id", ""),
            "action": action, "risky": risky, "visible": vis,
            "expect": expect,
        })

    # link: href 存在
    for el in groups.get("link", []):
        if el["sel"] in seen_sels:
            continue
        seen_sels.add(el["sel"])
        cid += 1
        cases.append({
            "id": f"c{cid:03d}", "type": "link",
            "selector": el["sel"], "label": (el.get("text") or el.get("href",""))[:40],
            "action": "verify_href", "risky": False,
            "expect": "href 属性非空且为合法 URL",
            "href": el.get("href", ""),
        })

    # input: 验证存在 + placeholder, 不真填 (避免破数据)
    for el in groups.get("input", []):
        if el["sel"] in seen_sels:
            continue
        seen_sels.add(el["sel"])
        cid += 1
        cases.append({
            "id": f"c{cid:03d}", "type": "input",
            "selector": el["sel"], "label": el.get("placeholder") or el.get("id",""),
            "action": "verify_input", "risky": False,
            "expect": "字段存在 + type 合法",
            "type": el.get("type", ""), "placeholder": el.get("placeholder", ""),
        })

    # select: 选项数检查
    for el in groups.get("select", []):
        if el["sel"] in seen_sels:
            continue
        seen_sels.add(el["sel"])
        cid += 1
        cases.append({
            "id": f"c{cid:03d}", "type": "select",
            "selector": el["sel"], "label": el.get("id", ""),
            "action": "verify_select", "risky": False,
            "expect": "select 存在且 ≥ 1 个选项",
        })

    # textarea: 检查存在 + placeholder
    for el in groups.get("textarea", []):
        if el["sel"] in seen_sels:
            continue
        seen_sels.add(el["sel"])
        cid += 1
        cases.append({
            "id": f"c{cid:03d}", "type": "textarea",
            "selector": el["sel"], "label": el.get("placeholder") or el.get("id", ""),
            "action": "verify_textarea", "risky": False,
            "expect": "textarea 存在 + placeholder 非空",
        })

    # onclick 元素 (div/span 等)
    for el in groups.get("onclick", []):
        if el["sel"] in seen_sels:
            continue
        seen_sels.add(el["sel"])
        cid += 1
        text = (el.get("text") or "").strip()
        risky = any(k in text.lower() for k in _HIGH_RISK)
        vis = el.get("visible")
        cases.append({
            "id": f"c{cid:03d}", "type": "onclick",
            "selector": el["sel"], "label": text[:30],
            "action": "exists_only" if (risky or not vis) else "click",
            "risky": risky, "visible": vis,
            "expect": "onclick 元素点击后无 JS 报错",
        })

    return cases


def step_run_case(browser_url: str, case: dict, ts_dir: Path,
                  do_vision: bool, server_url: str, token: str) -> dict:
    """4. 跑单个 case → before/after 截图 → (可选) vision_check"""
    cid = case["id"]
    sel = case["selector"]
    action = case["action"]
    result: dict[str, Any] = {**case}

    # before 截图 (shot b64=None 时 reload 重试 → 第二次失败再 goto)
    rb = _post_act(browser_url, "shot", {}, shot=True, wait_ms=200)
    if not rb.get("b64"):
        time.sleep(0.3)
        rb = _post_act(browser_url, "shot", {}, shot=True, wait_ms=400)
    if not rb.get("b64"):
        # chromium 帧错乱 → goto 强重置
        web_url_env = os.environ.get("LITECODE_WEB_URL", "http://127.0.0.1:18790")
        _post_act(browser_url, "goto", {"url": web_url_env},
                  wait_ms=800, timeout=10)
        time.sleep(0.5)
        rb = _post_act(browser_url, "shot", {}, shot=True, wait_ms=400)
    if rb.get("b64"):
        _save_b64_png(rb["b64"], ts_dir / f"snap_{cid}_before.png")
        result["snap_before"] = f"snap_{cid}_before.png"

    # 执行 action
    if action == "click":
        ra = _post_act(browser_url, "click", {"sel": sel}, wait_ms=400, timeout=8)
        result["action_ok"] = ra.get("ok")
        result["action_msg"] = (ra.get("msg") or "")[:160]
    elif action == "exists_only":
        rf = _post_act(browser_url, "count", {"sel": sel}, wait_ms=100)
        cnt = rf.get("data", 0) or 0
        result["action_ok"] = bool(cnt and cnt >= 1)
        result["action_msg"] = f"count={cnt}"
    elif action == "verify_href":
        href = case.get("href", "")
        result["action_ok"] = bool(href and (href.startswith("http") or href.startswith("/") or href.startswith("#")))
        result["action_msg"] = f"href={href[:80]}"
    elif action == "verify_input":
        rf = _post_act(browser_url, "count", {"sel": sel}, wait_ms=100)
        cnt = rf.get("data", 0) or 0
        result["action_ok"] = bool(cnt and cnt >= 1) and bool(case.get("type"))
        result["action_msg"] = f"count={cnt} type={case.get('type')}"
    elif action == "verify_select":
        rf = _post_act(browser_url, "eval",
                       {"script": f"document.querySelector('{sel}')?.options?.length || 0"})
        n = rf.get("data", 0)
        result["action_ok"] = bool(isinstance(n, int) and n >= 1)
        result["action_msg"] = f"options={n}"
    elif action == "verify_textarea":
        rf = _post_act(browser_url, "count", {"sel": sel}, wait_ms=100)
        cnt = rf.get("data", 0) or 0
        result["action_ok"] = bool(cnt and cnt >= 1)
        result["action_msg"] = f"count={cnt}"
    else:
        result["action_ok"] = False
        result["action_msg"] = f"unknown action: {action}"

    # after 截图 (shot b64=None 时 retry → goto 强重置)
    time.sleep(0.3)
    ra2 = _post_act(browser_url, "shot", {}, shot=True, wait_ms=200)
    if not ra2.get("b64"):
        time.sleep(0.3)
        ra2 = _post_act(browser_url, "shot", {}, shot=True, wait_ms=400)
    if not ra2.get("b64"):
        web_url_env = os.environ.get("LITECODE_WEB_URL", "http://127.0.0.1:18790")
        _post_act(browser_url, "goto", {"url": web_url_env},
                  wait_ms=800, timeout=10)
        time.sleep(0.5)
        ra2 = _post_act(browser_url, "shot", {}, shot=True, wait_ms=400)
    if ra2.get("b64"):
        _save_b64_png(ra2["b64"], ts_dir / f"snap_{cid}_after.png")
        result["snap_after"] = f"snap_{cid}_after.png"

    # vision_check (可选): 只对真 click 类跑 vision, exists_only/verify_* 不需要 vision
    needs_vision = (do_vision and result.get("snap_after")
                    and action == "click")
    if needs_vision:
        try:
            vresp, vlat = _vision_check(server_url, token,
                                        ts_dir / result["snap_after"],
                                        case["expect"])
            result["vision_raw"] = (vresp or "")[:300]
            result["vision_latency_ms"] = vlat
            vlow = (vresp or "").lower()
            verdict = "PASS" if ("pass" in vlow and "true" in vlow) else "FAIL"
            result["vision_verdict"] = verdict
            (ts_dir / f"vision_{cid}.json").write_text(json.dumps({
                "case_id": cid, "expect": case["expect"],
                "raw": (vresp or "")[:500], "verdict": verdict,
                "latency_ms": vlat,
            }, ensure_ascii=False, indent=2))
        except Exception as e:
            result["vision_verdict"] = "ERROR"
            result["vision_raw"] = str(e)[:200]
    elif do_vision:
        result["vision_verdict"] = "N/A"  # 非 click 不需 vision
    else:
        result["vision_verdict"] = "SKIP"

    # 最终 case status
    if action == "exists_only":
        result["status"] = "PASS" if result["action_ok"] else "FAIL"
    elif result["action_ok"]:
        result["status"] = "PASS"
    else:
        result["status"] = "FAIL"

    # click 类还要看 vision 判断 (vision 不算 SKIP/ERROR 才考虑)
    if action == "click" and result.get("vision_verdict") in ("PASS", "FAIL"):
        # vision FAIL 时降级为 SUSPICIOUS, 提示可能 bug
        if result["vision_verdict"] == "FAIL" and result["status"] == "PASS":
            result["status"] = "SUSPICIOUS"

    return result


def _vision_check(server_url: str, token: str, jpg_path: Path,
                  expect: str, timeout: int = 60) -> tuple[str, int]:
    """走 chat /v1/chat/completions, vision-route 自动 fallback"""
    if not jpg_path.exists():
        return "", 0
    mime = "image/png"
    b64 = base64.b64encode(jpg_path.read_bytes()).decode("ascii")
    body = {
        "model": "deepseek-v4-pro", "stream": False,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text":
             f"判断截图是否符合: {expect}. 用 JSON 回复 {{\"pass\": bool, \"reason\": 短理由}}"}
        ]}],
    }
    t0 = time.time()
    req = urllib.request.Request(
        f"{server_url}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        content = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        return str(content)[:800], int((time.time() - t0) * 1000)
    except Exception as e:
        return f"ERROR: {e}", int((time.time() - t0) * 1000)


def render_index_html(ts_dir: Path, summary: dict, cases_results: list) -> Path:
    """5. 生成 index.html — 表格 + 缩略图 + 跳转锚"""
    def _esc(s: str) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    rows = []
    for r in cases_results:
        cid = r.get("id", "?")
        st = r.get("status", "?")
        color = {"PASS": "#26d07c", "FAIL": "#ff5a5a",
                 "SUSPICIOUS": "#ffb347", "ERROR": "#888"}.get(st, "#999")
        before = r.get("snap_before", "")
        after = r.get("snap_after", "")
        rows.append(f"""
<tr id="case-{cid}">
  <td><b>{cid}</b></td>
  <td>{_esc(r.get('type',''))}</td>
  <td><code>{_esc(r.get('selector','')[:60])}</code></td>
  <td>{_esc(r.get('label','')[:40])}</td>
  <td>{_esc(r.get('action',''))}</td>
  <td style="color:{color};font-weight:bold">{st}</td>
  <td>{_esc(r.get('action_msg','')[:80])}</td>
  <td>{_esc((r.get('vision_verdict','') or '-'))}</td>
  <td><img src="{before}" style="width:120px;height:auto" loading="lazy"></td>
  <td><img src="{after}"  style="width:120px;height:auto" loading="lazy"></td>
</tr>""")

    counts = summary.get("counts", {})
    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8">
<title>Auto Web Test Report — {summary.get('ts','?')}</title>
<style>
  body {{ font: 14px/1.5 -apple-system, sans-serif; padding: 20px;
         background: #1a1a1a; color: #eee; }}
  h1 {{ color: #6ec0ff; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
  th, td {{ border: 1px solid #444; padding: 6px 10px; vertical-align: top; }}
  th {{ background: #2a2a2a; }}
  code {{ background: #2a2a2a; padding: 2px 5px; border-radius: 3px;
          font-size: 12px; }}
  .stats {{ display: flex; gap: 16px; margin: 12px 0; }}
  .stat {{ background: #2a2a2a; padding: 10px 16px; border-radius: 6px; }}
  .pass {{ color: #26d07c; }}
  .fail {{ color: #ff5a5a; }}
  .susp {{ color: #ffb347; }}
</style>
</head><body>
<h1>🤖 Auto Web Test Report</h1>
<p>时间: {summary.get('ts','?')} &nbsp; 总用时: {summary.get('total_seconds',0):.1f}s</p>
<div class="stats">
  <div class="stat">总用例: <b>{summary.get('total_cases',0)}</b></div>
  <div class="stat pass">PASS: <b>{counts.get('PASS',0)}</b></div>
  <div class="stat fail">FAIL: <b>{counts.get('FAIL',0)}</b></div>
  <div class="stat susp">SUSPICIOUS: <b>{counts.get('SUSPICIOUS',0)}</b></div>
  <div class="stat">ERROR: <b>{counts.get('ERROR',0)}</b></div>
</div>
<div class="stats">
  <div class="stat">元素 deep_inspect: <b>{summary.get('total_elements',0)}</b></div>
  <div class="stat">截图: <b>{summary.get('snap_count',0)}</b></div>
  <div class="stat">vision_*.json: <b>{summary.get('vision_count',0)}</b></div>
</div>
<table>
<tr><th>#</th><th>类型</th><th>selector</th><th>标签</th><th>action</th>
    <th>status</th><th>msg</th><th>vision</th><th>before</th><th>after</th></tr>
{"".join(rows)}
</table>
</body></html>
"""
    idx = ts_dir / "index.html"
    idx.write_text(html, encoding="utf-8")
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:18789",
                    help="LiteCode server URL (for vision_check)")
    ap.add_argument("--web", default="http://127.0.0.1:18790",
                    help="LiteCode Web UI URL (登录目标)")
    ap.add_argument("--browser", default="http://127.0.0.1:19000",
                    help="browser_server URL (Node + puppeteer)")
    ap.add_argument("--password", default=os.environ.get("WEB_PASSWORD") or "CHANGE_ME_PASSWORD",
                    help="Web UI 登录密码")
    ap.add_argument("--token", default=os.environ.get("SERVER_TOKEN") or "CHANGE_ME_TOKEN",
                    help="server token (vision_check 用)")
    ap.add_argument("--skip-vision", action="store_true",
                    help="不跑 vision_check (快速)")
    ap.add_argument("--max-cases", type=int, default=50,
                    help="限制用例数 (默认 50, 0=全跑)")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    ts_dir = REPORTS_DIR / ts
    ts_dir.mkdir(parents=True, exist_ok=True)
    print(f"[init] reports dir: {ts_dir}")

    t0 = time.time()

    # 1. login
    r_login = step_login(args.browser, args.web, args.password)
    if not r_login.get("ok"):
        print(f"[ERROR] login 失败: {r_login.get('msg')}")
        sys.exit(2)
    print(f"[ok] login: {r_login.get('msg')}")

    # 2. deep_inspect
    elements = step_inspect_elements(args.browser)
    if not elements.get("ok"):
        print(f"[ERROR] deep_inspect 失败: {elements.get('msg')}")
        sys.exit(3)
    total_elements = elements.get("total", 0)
    print(f"[ok] deep_inspect: {total_elements} 元素")
    (ts_dir / "elements.json").write_text(json.dumps(elements, ensure_ascii=False, indent=2))

    # 3. generate cases
    cases = step_generate_cases(elements)
    print(f"[ok] generate cases: {len(cases)}")
    if args.max_cases and len(cases) > args.max_cases:
        cases = cases[:args.max_cases]
        print(f"[debug] 截断到 {len(cases)} cases")
    (ts_dir / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2))

    # 4. run each case — 每 6 个 click case 之后 goto 重置 chromium 状态
    #   防 click 累积让页面 navigated / chromium 帧错乱 → shot 返 b64=None → 缺图
    results = []
    do_vision = not args.skip_vision
    click_streak = 0
    for i, case in enumerate(cases, 1):
        if click_streak >= 6:
            print(f"  [reset] goto 重置 chromium ({click_streak} click 后)")
            _post_act(args.browser, "goto", {"url": args.web},
                      session="auto_test", wait_ms=1000, timeout=15)
            click_streak = 0
        print(f"  [{i}/{len(cases)}] {case['id']} {case['type']} {case['selector'][:40]}")
        r = step_run_case(args.browser, case, ts_dir,
                          do_vision, args.server, args.token)
        results.append(r)
        if case.get("action") == "click":
            click_streak += 1

    # 5. summary + index.html
    counts = {"PASS": 0, "FAIL": 0, "SUSPICIOUS": 0, "ERROR": 0}
    for r in results:
        counts[r.get("status", "ERROR")] = counts.get(r.get("status", "ERROR"), 0) + 1
    snap_count = sum(1 for r in results
                     if r.get("snap_before") and r.get("snap_after"))
    vision_count = sum(1 for r in results if r.get("vision_verdict") in ("PASS", "FAIL"))
    total_seconds = time.time() - t0
    summary = {
        "ts": ts, "total_seconds": total_seconds,
        "total_elements": total_elements, "total_cases": len(cases),
        "snap_count": snap_count * 2, "vision_count": vision_count,
        "counts": counts,
    }
    (ts_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    (ts_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    idx = render_index_html(ts_dir, summary, results)
    print(f"\n[done] {summary['total_cases']} cases / "
          f"PASS={counts['PASS']} FAIL={counts['FAIL']} "
          f"SUSPICIOUS={counts['SUSPICIOUS']} ERROR={counts['ERROR']}")
    print(f"[done] 截图 {summary['snap_count']} 张, vision_*.json {vision_count}")
    print(f"[done] 用时 {total_seconds:.1f}s")
    print(f"[done] 报告: {idx}")


if __name__ == "__main__":
    main()

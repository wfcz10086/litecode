#!/usr/bin/env python3
"""E2E: 当前对话底部「⏳ 后台进行中」卡片 (web_assets/bg_card.js)。

隔离测试实例 (复用 e2e_newchat_during_stream.py 的配置/启动/端口断言, 不碰生产 18789/18790):
  假模型 → litecode_server → web_ui 都在随机空闲端口, LITECODE_CONFIG 指向临时配置副本。
假模型: 用户消息含
  BGTASK  → execute_shell(background=true) 跑工作区里的假 anime_short.py (逐行输出 [帧]/[视频] 进度, ~60s)
  BGSTOP  → 同上但跑很久 (用来点「停止」)
  BGFAIL  → 同上, 5 秒后 exit 2
  SLOWTASK→ 前台 execute_shell sleep 8 (这一轮流式输出持续 8 秒)
  其余 → 秒回一句话

步骤 (playwright headless, 真人式点击):
  1. 会话 A 发 BGTASK → 轮次结束后卡片出现: 说明 "anime_short.py mv …/捡可乐瓶_写实" + 时长 + 最后一条进度行
  2. 点开 → 最近 20 行日志
  3. 手机宽度 390px 不溢出
  4. A 里发 SLOWTASK, 流式进行中卡片照常显示
  5. 新会话 B: 卡片不显示 A 的任务; 发 BGSTOP → 点「停止」→ confirmCard 确认 → 显示已停止, 进程真被结束
     再发 BGFAIL → "❌ 失败 (code=2)"
  6. 切回 A → 等任务结束显示 "✅ 已完成" + 提示; 30 秒后自动收起
用法: python3 scripts/e2e_bg_card.py [--shots DIR] [--keep]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_newchat_during_stream as base  # noqa: E402

PROD_PORTS = base.PROD_PORTS
OK_TEXT = "已放到后台运行, 预计 1 分钟, 问我「好了吗」即可。"
QUICK_TEXT = "收到。"

FAKE_MV = r'''
import sys, time
a = sys.argv[1:]
secs = int(a[a.index("--secs") + 1]); code = int(a[a.index("--code") + 1])
for i in range(1, 5):
    print(f"[帧] {i:02d} 第 1 次 {10 + i}s 审阅: ✓", flush=True)
    time.sleep(min(3, secs / 8))
print("", flush=True)
print("[视频] 03 h3 90s 审阅: ✓", flush=True)
print("   ", flush=True)
time.sleep(max(1, secs - 12))
print("[合成] 成片 out.mp4", flush=True)
sys.exit(code)
'''


def role_fake_llm(port, ws):
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    app = FastAPI()
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def mv(secs, code, name):
        return {"command": f"python3 {ws}/anime_short.py mv --out {ws}/out/{name} --secs {secs} --code {code}",
                "background": True}

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": "fake-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        msgs = body.get("messages") or []
        last = msgs[-1] if msgs else {}
        lu = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
        lu_text = json.dumps(lu.get("content"), ensure_ascii=False)
        args, text = None, QUICK_TEXT
        if last.get("role") == "tool":
            text = OK_TEXT
        elif "BGTASK" in lu_text:
            args = mv(60, 0, "捡可乐瓶_写实")
        elif "BGSTOP" in lu_text:
            args = mv(600, 0, "长任务_待停止")
        elif "BGFAIL" in lu_text:
            args = mv(13, 2, "会失败的任务")
        elif "SLOWTASK" in lu_text:
            args = {"command": "sleep 8 && echo slow-done"}
        tool_call = None
        if args:
            text = ""
            tool_call = {"index": 0, "id": "call_%d" % int(time.time() * 1000), "type": "function",
                         "function": {"name": "execute_shell", "arguments": json.dumps(args, ensure_ascii=False)}}

        def chunk(delta, fin=None, extra=None):
            d = {"id": "x", "object": "chat.completion.chunk", "model": "fake-model",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
            d.update(extra or {})
            return "data: " + json.dumps(d, ensure_ascii=False) + "\n\n"

        def gen():
            if tool_call:
                yield chunk({"role": "assistant", "content": None, "tool_calls": [tool_call]})
                yield chunk({}, "tool_calls", {"usage": usage})
            else:
                yield chunk({"role": "assistant", "content": text[:3]})
                yield chunk({"content": text[3:]})
                yield chunk({}, "stop", {"usage": usage})
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def start_instance(d: Path):
    srv, web, llm = base._free_port(), base._free_port(), base._free_port()
    assert not ({srv, web, llm} & PROD_PORTS)
    pw = "e2e-bgcard-pw"
    cfg = base.make_config(d, srv, web, llm, pw)
    for sub in ("home", "ws/sessions", "ws/logs", "ws/out"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "ws" / "anime_short.py").write_text(FAKE_MV)
    c = json.loads(cfg.read_text())
    assert c["server"]["port"] == srv and c["web_ui"]["port"] == web and f":{llm}/" in c["model"]["backend_url"]
    env = dict(os.environ, LITECODE_CONFIG=str(cfg), HOME=str(d / "home"), WORKSPACE=str(d / "ws"),
               PYTHONUNBUFFERED="1")
    me = str(Path(__file__).resolve())
    procs = []
    for args, port in ((["--role", "llm", str(llm), str(d / "ws")], llm),
                       (["--role", "server", str(srv), str(llm)], srv),
                       (["--role", "web", str(web), str(srv)], web)):
        log = open(d / f"{args[1]}.log", "w")
        p = subprocess.Popen([sys.executable, me, *args], env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        base._wait_port(port, p)
    return procs, f"http://127.0.0.1:{web}", pw


def run_browser(url, pw, shots: Path):
    from playwright.sync_api import expect, sync_playwright
    shots.mkdir(parents=True, exist_ok=True)
    errs = []
    card = "#bgc"

    def send(pg, text):
        pg.fill("#mi", text)
        pg.press("#mi", "Enter")

    def wait_idle(pg, timeout=30000):
        pg.wait_for_function("() => window.streaming === false", timeout=timeout)

    def procs(pg):
        return pg.evaluate("async () => (await (await fetch('/api/bgprocs')).json()).procs")

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            pg = b.new_context(viewport={"width": 1400, "height": 900}).new_page()
            pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
            pg.goto(url + "/")
            pg.fill("#lpwd", pw)
            pg.click(".lb_")
            expect(pg.locator("#mi")).to_be_visible(timeout=20000)
            ncb = pg.locator("#ncb")

            # 1. 会话 A 起后台任务 → 卡片
            ncb.click()
            pg.wait_for_function("() => !!cur", timeout=5000)
            expect(pg.locator(card)).to_be_hidden()
            send(pg, "BGTASK 帮我做个捡可乐瓶的 MV")
            expect(pg.locator("#msgs .msg.ai").last).to_contain_text(OK_TEXT, timeout=30000)
            wait_idle(pg)
            sid_a = pg.evaluate("cur")
            expect(pg.locator(card)).to_be_visible(timeout=5000)        # 轮次结束立即刷新, 不等 10 秒
            expect(pg.locator(f"{card} .bgc-hd")).to_contain_text("后台进行中")
            expect(pg.locator(f"{card} .bgc-ds")).to_have_text("anime_short.py mv …/捡可乐瓶_写实")
            expect(pg.locator(f"{card} .bgc-ln")).to_contain_text("[帧]", timeout=12000)
            expect(pg.locator(f"{card} .bgc-el")).to_contain_text("秒")
            # 10 秒刷新: 进度行前进到 [视频]
            expect(pg.locator(f"{card} .bgc-ln")).to_contain_text("[视频] 03 h3 90s 审阅: ✓", timeout=25000)
            pg.screenshot(path=str(shots / "01_running.png"))

            # 2. 展开日志
            pg.locator(f"{card} .bgc-tg").click()
            log = pg.locator(f"{card} .bgc-log")
            expect(log).to_be_visible()
            expect(log).to_contain_text("[帧] 01 第 1 次 11s 审阅: ✓")
            expect(log).to_contain_text("[视频] 03")
            assert len(log.inner_text().splitlines()) <= 20
            pg.screenshot(path=str(shots / "02_log_expanded.png"))

            # 3. 手机宽度
            pg.set_viewport_size({"width": 390, "height": 844})
            pg.wait_for_timeout(600)
            ov = pg.evaluate("""() => { const r = document.querySelector('#bgc').getBoundingClientRect();
                return {sw: document.documentElement.scrollWidth, iw: innerWidth, right: r.right, left: r.left}; }""")
            assert ov["sw"] <= ov["iw"] and ov["right"] <= ov["iw"] + 0.5 and ov["left"] >= 0, ov
            pg.screenshot(path=str(shots / "03_mobile_390.png"))
            pg.set_viewport_size({"width": 1400, "height": 900})
            pg.locator(f"{card} .bgc-tg").click()          # 收起
            expect(log).to_be_hidden()

            # 4. 流式进行中照常显示
            send(pg, "SLOWTASK 前台跑一下")
            pg.wait_for_function("() => window.streaming === true", timeout=10000)
            pg.wait_for_timeout(1500)
            assert pg.evaluate("window.streaming") is True
            expect(pg.locator(card)).to_be_visible()
            expect(pg.locator(f"{card} .bgc-ds")).to_have_text("anime_short.py mv …/捡可乐瓶_写实")
            wait_idle(pg, 40000)

            # 5. 会话 B: 不显示 A 的任务; 停止 / 失败
            ncb.click()
            pg.wait_for_function("(a) => cur && cur !== a", arg=sid_a, timeout=5000)
            pg.wait_for_timeout(1500)
            expect(pg.locator(card)).to_be_hidden()
            send(pg, "BGSTOP 跑个很长的")
            expect(pg.locator("#msgs .msg.ai").last).to_contain_text(OK_TEXT, timeout=30000)
            wait_idle(pg)
            sid_b = pg.evaluate("cur")
            expect(pg.locator(f"{card} .bgc-ds")).to_have_text("anime_short.py mv …/长任务_待停止", timeout=5000)
            assert pg.locator(f"{card} .bgc-it").count() == 1           # 只有 B 自己的
            pid_b = pg.locator(f"{card} .bgc-it").get_attribute("data-pid")
            pg.locator(f"{card} .bgc-stop").click()
            expect(pg.locator(".cfm-mask")).to_be_visible()
            pg.locator(".cfm-mask .cancel").click()                     # 先取消: 不停
            pg.wait_for_timeout(500)
            assert next(x for x in procs(pg) if x["pid"] == pid_b)["alive"] is True
            pg.locator(f"{card} .bgc-stop").click()
            pg.locator(".cfm-mask .primary").click()
            expect(pg.locator(f"{card} .bgc-st")).to_contain_text("已停止", timeout=8000)
            st = next(x for x in procs(pg) if x["pid"] == pid_b)
            assert st["alive"] is False and st["stopped"] is True and st["sid"] == sid_b, st
            assert subprocess.run(["pgrep", "-f", "长任务_待停止"], capture_output=True).returncode != 0, "子进程没停"
            send(pg, "BGFAIL 这个会失败")
            wait_idle(pg)
            expect(pg.locator(f"{card} .bgc-st.bad")).to_contain_text("❌ 失败 (code=2)", timeout=35000)
            pg.screenshot(path=str(shots / "05_B_stopped_failed.png"))

            # 6. 切回 A: 等完成 → 30 秒后收起
            pg.locator(f'#sl .si[data-sid="{sid_a}"]').click()
            pg.wait_for_function("(a) => cur === a", arg=sid_a, timeout=5000)
            pg.wait_for_function("() => [...document.querySelectorAll('#bgc .bgc-ds')].map(e => e.textContent)"
                                 ".join('|') === 'anime_short.py mv …/捡可乐瓶_写实'", timeout=5000)   # 跟着切: 只剩 A 的
            expect(pg.locator(f"{card} .bgc-st.ok")).to_contain_text("✅ 已完成", timeout=70000)
            expect(pg.locator(f"{card} .bgc-hint")).to_contain_text("好了吗")
            expect(pg.locator(f"{card} .bgc-ln")).to_contain_text("[合成] 成片 out.mp4")
            pg.screenshot(path=str(shots / "04_done.png"))
            t0 = time.time()
            expect(pg.locator(card)).to_be_hidden(timeout=45000)
            hide_s = round(time.time() - t0, 1)
            assert not errs, errs
            return {"A": sid_a, "B": sid_b, "hidden_after_s": hide_s}
        finally:
            b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--shots", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    if a.role == "llm":
        return role_fake_llm(int(a.rest[0]), a.rest[1])
    if a.role == "server":
        return base.role_server(int(a.rest[0]), int(a.rest[1]))
    if a.role == "web":
        return base.role_web(int(a.rest[0]), int(a.rest[1]))
    d = Path(tempfile.mkdtemp(prefix="lc_e2e_bgcard_"))
    shots = Path(a.shots) if a.shots else d / "shots"
    procs, ok = [], False
    try:
        procs, url, pw = start_instance(d)
        print("测试实例:", url, "临时目录:", d, flush=True)
        r = run_browser(url, pw, shots)
        print("PASS", json.dumps(r, ensure_ascii=False), "截图:", shots, flush=True)
        ok = True
    except Exception as e:
        print("FAIL", type(e).__name__, str(e)[:2000], "截图:", shots, "日志:", d, flush=True)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except Exception:
                p.kill()
        subprocess.run(["pkill", "-f", f"{d}/ws/anime_short.py"], capture_output=True)
        if ok and not a.keep and not a.shots:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)

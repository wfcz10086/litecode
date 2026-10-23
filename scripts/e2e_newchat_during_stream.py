#!/usr/bin/env python3
"""回归 E2E: 长任务进行中点「新对话」不能卡住 (BACKLOG Inbox 2026-10-23)。

自带一套隔离测试实例, 不碰生产 (18789/18790):
  假模型 (OpenAI 兼容) → litecode_server → web_ui, 三者都在随机空闲端口, 配置 / HOME / 工作区都在临时目录;
  启动时断言 server 端口 / 模型地址 / web 转发地址都是本次的端口, 绝不会转发到生产。
假模型: 用户消息含 LONGTASK → 返回一个 execute_shell `sleep N` 的工具调用 (轮次持续 N 秒); 其余消息秒回一句话。

步骤 (playwright headless, 真人式点击):
  1. 历史会话 H: 发一句话, 等回复完成
  2. 会话 A: 发 LONGTASK, 等工具调用开始 (流还没结束)
  3. 点「新对话」→ 2 秒内出现空的新对话; 输入并发送, 收到回复
  4. 切回 A → A 仍在进行并继续接收 (重新接上实时进度)
  5. A 进行中切到 H → 2 秒内显示 H 的历史
  6. 再切回 A 等长任务结束, 看到最终回复

用法: python3 scripts/e2e_newchat_during_stream.py [--shots DIR] [--sleep 25] [--keep]
退出码 0 = 全部通过。pytest 包装见 litecodeext/tests/test_e2e_newchat_during_stream.py (默认 skip)。
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LCX = REPO / "litecodeext"
PROD_PORTS = {18789, 18790}
FINAL_TEXT = "长任务完成, 输出 done。"
QUICK_TEXT = "收到, 这是快速回复。"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    assert p not in PROD_PORTS
    return p


# ── 子进程角色 ────────────────────────────────────────────────
def role_fake_llm(port, sleep_s):
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    app = FastAPI()
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

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
        tool_call = None
        if last.get("role") == "tool":
            text = FINAL_TEXT
        elif "LONGTASK" in lu_text:
            text = ""
            tool_call = {"index": 0, "id": "call_long_1", "type": "function",
                         "function": {"name": "execute_shell",
                                      "arguments": json.dumps({"command": f"sleep {sleep_s} && echo done"})}}
        else:
            text = QUICK_TEXT
        if not body.get("stream"):
            msg = {"role": "assistant", "content": text or ""}
            if tool_call:
                msg["tool_calls"] = [{k: v for k, v in tool_call.items() if k != "index"}]
            return JSONResponse({"id": "x", "object": "chat.completion", "model": "fake-model", "usage": usage,
                                 "choices": [{"index": 0, "message": msg,
                                              "finish_reason": "tool_calls" if tool_call else "stop"}]})

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
                yield chunk({"role": "assistant", "content": text[:4]})
                yield chunk({"content": text[4:]})
                yield chunk({}, "stop", {"usage": usage})
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def role_server(port, llm_port):
    sys.path.insert(0, str(LCX))
    os.chdir(LCX)
    import uvicorn
    import litecode_server
    from lib import config
    assert config.PORT == port and f":{llm_port}" in config.BACKEND_URL, (config.PORT, config.BACKEND_URL)
    uvicorn.run(litecode_server.app, host="127.0.0.1", port=port, log_level="warning")


def role_web(port, srv_port):
    sys.path.insert(0, str(LCX))
    os.chdir(LCX)
    import uvicorn
    import web_ui
    web_ui._WX_AVAILABLE = web_ui._WC_AVAILABLE = web_ui._TIMER_AVAILABLE = False
    web_ui._DAG_JOBS.clear()
    assert web_ui.SERVER_URL.endswith(f":{srv_port}"), web_ui.SERVER_URL   # 绝不能转发到生产 18789
    from routers.state import S
    assert str(S.cfg_path) == os.environ["LITECODE_CONFIG"], S.cfg_path
    uvicorn.run(web_ui.app, host="127.0.0.1", port=port, log_level="warning")


# ── 测试实例 ──────────────────────────────────────────────────
def make_config(d: Path, srv, web, llm, pw):
    c = json.loads((LCX / "config.json").read_text())
    fake = {"id": "fake-model", "label": "假模型", "backend_type": "openai",
            "backend_url": f"http://127.0.0.1:{llm}/v1", "api_key": "EMPTY", "api_format": "openai-chat",
            "context_window": 128000, "max_tokens": 2048, "enable_thinking": False, "supports_vision": False}
    c["server"] = {"host": "127.0.0.1", "port": srv, "token": "e2etoken"}
    c["model"], c["models"], c["default_model_id"] = dict(fake), [dict(fake)], "fake-model"
    c["vision_fallback_id"], c["side_models"], c["providers"] = "", {}, []
    ws = d / "ws"
    c["paths"] = {"workspace_base": str(ws), "sessions_dir": str(ws / "sessions"), "logs_dir": str(ws / "logs")}
    c.setdefault("iteration_trace", {})["save_path"] = str(ws / "logs" / "iteration_full.log")
    c.setdefault("web_ui", {})["port"] = web
    c["web_ui"]["auth"] = {"enabled": True, "password": pw, "session_days": 1}
    for k in ("wechat", "wecom"):
        if isinstance(c.get(k), dict):
            c[k]["enabled"] = False
    (d / "config.json").write_text(json.dumps(c, ensure_ascii=False, indent=2))
    return d / "config.json"


def _wait_port(port, proc, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"端口 {port} 的进程提前退出 rc={proc.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"端口 {port} 等待超时")


def start_instance(d: Path, sleep_s):
    srv, web, llm = _free_port(), _free_port(), _free_port()
    pw = "e2e-newchat-pw"
    cfg = make_config(d, srv, web, llm, pw)
    for sub in ("home", "ws/sessions", "ws/logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LITECODE_CONFIG=str(cfg), HOME=str(d / "home"), WORKSPACE=str(d / "ws"),
               PYTHONUNBUFFERED="1")
    me = str(Path(__file__).resolve())
    procs = []
    for args, port in ((["--role", "llm", str(llm), str(sleep_s)], llm),
                       (["--role", "server", str(srv), str(llm)], srv),
                       (["--role", "web", str(web), str(srv)], web)):
        log = open(d / f"{args[1]}.log", "w")
        p = subprocess.Popen([sys.executable, me, *args], env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        _wait_port(port, p)
    return procs, f"http://127.0.0.1:{web}", pw


# ── playwright 步骤 ───────────────────────────────────────────
def run_browser(base, pw, shots: Path, sleep_s):
    from playwright.sync_api import expect, sync_playwright
    shots.mkdir(parents=True, exist_ok=True)
    errs, n = [], [0]

    def shot(pg, name):
        n[0] += 1
        pg.screenshot(path=str(shots / f"{n[0]:02d}_{name}.png"))

    def send(pg, text):
        pg.fill("#mi", text)
        pg.press("#mi", "Enter")

    def wait_idle(pg, timeout=30000):
        pg.wait_for_function("() => window.streaming === false", timeout=timeout)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            pg = b.new_context(viewport={"width": 1400, "height": 900}).new_page()
            pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
            pg.goto(base + "/")
            pg.fill("#lpwd", pw)
            pg.click(".lb_")
            expect(pg.locator("#mi")).to_be_visible(timeout=20000)
            ncb = pg.locator("#ncb")

            # 1. 历史会话 H
            ncb.click()
            pg.wait_for_function("() => !!cur", timeout=5000)
            send(pg, "历史会话 H 的第一句")
            expect(pg.locator("#msgs .msg.ai").last).to_contain_text(QUICK_TEXT, timeout=20000)
            wait_idle(pg)
            sid_h = pg.evaluate("cur")

            # 2. 会话 A 开长任务
            ncb.click()
            pg.wait_for_function("(h) => cur && cur !== h", arg=sid_h, timeout=5000)
            send(pg, "LONGTASK 跑一个长任务")
            pg.wait_for_function("() => window.streaming === true", timeout=10000)
            expect(pg.locator("#msgs .msg.ai .tg").last).to_contain_text("sleep", timeout=20000)
            sid_a = pg.evaluate("cur")
            shot(pg, "A_long_task_running")

            # 3. 进行中点「新对话」→ 2 秒内出现空的新对话
            t0 = time.time()
            ncb.click()
            try:
                pg.wait_for_function(
                    "(a) => cur && cur !== a && !!document.querySelector('#msgs #empty')"
                    " && !document.querySelector('#msgs .msg') && window.streaming === false",
                    arg=sid_a, timeout=2000)
            except Exception:
                shot(pg, "BUG_newchat_stuck")
                raise AssertionError(f"长任务进行中点「新对话」2 秒内没出现新对话 (cur={pg.evaluate('cur')}, "
                                     f"A={sid_a}, streaming={pg.evaluate('window.streaming')})")
            dt = time.time() - t0
            sid_n = pg.evaluate("cur")
            shot(pg, "new_chat_ready")
            send(pg, "新对话里的第一句")
            expect(pg.locator("#msgs .msg.ai").last).to_contain_text(QUICK_TEXT, timeout=20000)
            wait_idle(pg)
            expect(pg.locator(f'#sl .si[data-sid="{sid_n}"]')).to_have_count(1)
            shot(pg, "new_chat_replied")

            # 4. 切回 A → 仍在进行并继续接收
            pg.locator(f'#sl .si[data-sid="{sid_a}"]').click()
            pg.wait_for_function("(a) => cur === a && window.streaming === true", arg=sid_a, timeout=8000)
            expect(pg.locator("#msgs .msg.ai .tg").last).to_contain_text("sleep", timeout=8000)
            pg.wait_for_timeout(1500)   # 等回放的淡入动画结束再截图
            shot(pg, "back_to_A_still_running")

            # 5. A 进行中切到历史会话 H → 2 秒内出历史
            pg.locator(f'#sl .si[data-sid="{sid_h}"]').click()
            expect(pg.locator("#msgs .msg.user").first).to_contain_text("历史会话 H 的第一句", timeout=2000)
            pg.wait_for_function("(h) => cur === h && window.streaming === false", arg=sid_h, timeout=2000)
            shot(pg, "switch_to_H")

            # 6. 回 A 等长任务结束
            pg.locator(f'#sl .si[data-sid="{sid_a}"]').click()
            expect(pg.locator("#msgs .msg.ai").last).to_contain_text(FINAL_TEXT, timeout=(sleep_s + 60) * 1000)
            wait_idle(pg)
            shot(pg, "A_finished")
            assert not errs, errs
            return {"newchat_seconds": round(dt, 2), "A": sid_a, "H": sid_h, "N": sid_n}
        finally:
            b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--shots", default="")
    ap.add_argument("--sleep", type=int, default=25, help="长任务 sleep 秒数")
    ap.add_argument("--keep", action="store_true", help="保留临时目录 (日志 / 会话)")
    a = ap.parse_args()
    if a.role == "llm":
        return role_fake_llm(int(a.rest[0]), int(a.rest[1]))
    if a.role == "server":
        return role_server(int(a.rest[0]), int(a.rest[1]))
    if a.role == "web":
        return role_web(int(a.rest[0]), int(a.rest[1]))
    d = Path(tempfile.mkdtemp(prefix="lc_e2e_newchat_"))
    shots = Path(a.shots) if a.shots else d / "shots"
    procs = []
    ok = False
    try:
        procs, base, pw = start_instance(d, a.sleep)
        print("测试实例:", base, "临时目录:", d, flush=True)
        r = run_browser(base, pw, shots, a.sleep)
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
        if ok and not a.keep and not a.shots:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)

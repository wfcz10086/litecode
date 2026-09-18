"""端到端 v2: 真等 AI 出图 + 自动点击预览卡展开 + 截图证明."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

URL_BASE = "http://127.0.0.1:18790"
PASSWORD = "CHANGE_ME_PASSWORD"
PROMPT   = (
    "请帮我做两件事:\n"
    "1) desktop_exec 打开 chromium 看 ETH 合约 (tradingview ETHUSDT.P)\n"
    "2) 等 6 秒后调 desktop_screenshot() 截图给我看\n"
    "完成后简短说一句 ✓ 完成"
)

OUT = Path("/tmp/litecode_workspace/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
shot_login    = OUT / f"e2ev2-1-login-{int(time.time())}.png"
shot_chatfull = OUT / f"e2ev2-2-chatfull-{int(time.time())}.png"
shot_expand   = OUT / f"e2ev2-3-expanded-{int(time.time())}.png"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True, executable_path="/usr/bin/google-chrome",
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.on("pageerror", lambda e: print(f"  [err] {e}"))

    # ── [1] 登录
    print(f"[1] 登录 {URL_BASE}")
    page.goto(URL_BASE, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_selector("#lpwd", timeout=10000, state="visible")
    page.screenshot(path=str(shot_login))
    page.fill("#lpwd", PASSWORD)
    page.click("button.lb_")
    page.wait_for_selector("#mi", timeout=15000, state="visible")
    page.wait_for_timeout(2000)
    print(f"   登录截图: {shot_login.name}")

    # ── [2] 对话
    print(f"[2] 输入 + 发送")
    page.fill("#mi", PROMPT)
    page.click("#sdb")

    # ── [3] 等 AI 真的输出截图路径 (而不是中间状态)
    # 判定: chat 文本里含 /tmp/litecode_workspace/screenshots/*.png 路径 + 不再 streaming
    print(f"[4] 等 AI 完成 (含截图路径), 最多 300s ...")
    start = time.time()
    found_path = None
    last_log = ""
    for _ in range(600):  # 0.5s × 600 = 300s
        try:
            state = page.evaluate("""() => {
                const ms = document.querySelectorAll('.msg.ai');
                const last = ms[ms.length-1];
                if (!last) return {n:0, txt:'', streaming:!!window.streaming};
                return {
                    n: ms.length,
                    txt: (last.textContent||''),
                    streaming: !!window.streaming,
                    cards: last.querySelectorAll('.pv-card').length,
                    imgs: last.querySelectorAll('img').length,
                };
            }""")
            txt = state.get("txt", "")
            # 找截图路径
            import re
            m = re.search(r'/tmp/litecode_workspace/screenshots/[\w\-./]+\.png', txt)
            log = (f"t={int(time.time()-start)}s ai={state['n']} streaming={state['streaming']} "
                   f"cards={state['cards']} imgs={state['imgs']} path={'YES' if m else 'no'}")
            if log != last_log:
                print(f"   {log}")
                last_log = log
            if m and not state['streaming']:
                found_path = m.group(0)
                # 同时要 cards > 0 (说明前端 _scanAndPreview 也已渲染)
                if state['cards'] > 0:
                    print(f"[5] AI 完成 + 预览卡已渲染 ({int(time.time()-start)}s)")
                    break
        except Exception as e:
            print(f"   poll err: {e}")
        page.wait_for_timeout(500)

    if not found_path:
        print("[5] WARN: 没等到截图路径, 仍尝试截图当前状态")
    page.wait_for_timeout(1000)
    page.screenshot(path=str(shot_chatfull), full_page=True)
    print(f"[6] 完整聊天截图: {shot_chatfull.name}")

    # ── [7] 点击最后一条 ai 消息里的预览卡 header 展开
    print(f"[7] 点击预览卡展开图片")
    clicked = page.evaluate("""() => {
        const ms = document.querySelectorAll('.msg.ai');
        const last = ms[ms.length-1];
        if (!last) return 'no_ai_msg';
        const cards = last.querySelectorAll('.pv-card');
        if (!cards.length) return 'no_cards';
        const card = cards[cards.length-1];
        const header = card.querySelector('div');  // header 是第一个 child div
        if (!header) return 'no_header';
        header.click();
        return `clicked_card_${cards.length}`;
    }""")
    print(f"   click result: {clicked}")
    # 等图片真加载
    try:
        page.wait_for_function("""() => {
            const ms = document.querySelectorAll('.msg.ai');
            const last = ms[ms.length-1];
            if (!last) return false;
            const imgs = last.querySelectorAll('img');
            if (!imgs.length) return false;
            return Array.from(imgs).some(i => i.complete && i.naturalWidth > 100);
        }""", timeout=15000)
        print("   图片已加载完整")
    except Exception as e:
        print(f"   图片加载超时: {e}")

    page.wait_for_timeout(2000)
    page.screenshot(path=str(shot_expand), full_page=True)
    print(f"[8] 展开图片截图: {shot_expand.name}")
    print(f"SHOT_FULL={shot_chatfull}")
    print(f"SHOT_EXPAND={shot_expand}")

    browser.close()

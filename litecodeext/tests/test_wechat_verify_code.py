"""wechatbot-sdk 0.3.0 配对码登录: 回调不阻塞事件循环, 等网页提交的配对码再轮询; 错码会再要一次 (2026-10-22)。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]
wechatbot = pytest.importorskip("wechatbot")
if not hasattr(wechatbot.WeChatBot.__init__, "__code__") or "on_verify_code" not in wechatbot.WeChatBot.__init__.__code__.co_varnames:
    pytest.skip("需要 wechatbot-sdk >= 0.3.0", allow_module_level=True)

from lib.wechat.lifecycle import WeChatLifecycleMixin  # noqa: E402


class Inst(WeChatLifecycleMixin):
    def __init__(self):
        self.bot_id, self.status, self._last_hint, self._last_error, self.qr_url = "t", "wait_scan", None, None, None


def test_code_flow(tmp_path):
    seen_codes = []

    async def run():
        inst = Inst()
        bot = wechatbot.WeChatBot(cred_path=str(tmp_path / "c.json"), on_qr_url=inst._on_qr_url,
                                  on_scanned=inst._on_scanned, on_expired=inst._on_expired,
                                  on_verify_code=inst._on_verify_code)
        inst._bot = bot
        api = bot._api
        answers = iter([{"status": "scaned"}, {"status": "need_verifycode"}, {"status": "need_verifycode"},
                        {"status": "scaned"}, {"status": "confirmed", "bot_token": "tok", "ilink_bot_id": "b@x",
                                                "baseurl": "https://example.invalid", "ilink_user_id": "u"}])

        async def fake_qr(base_url, local_token_list=None):
            return {"qrcode": "q", "qrcode_img_content": "https://qr.invalid/x"}

        async def fake_poll(base_url, qrcode, verify_code=None):
            seen_codes.append(verify_code)
            return next(answers)
        api.get_qr_code = fake_qr
        api.poll_qr_status = fake_poll
        inst._hook_verify_poll()

        async def user():
            while inst.status != "need_code":
                await asyncio.sleep(0.05)
            assert inst.submit_verify_code("12x") == {"ok": True}       # 先输错 (只取数字 → 12)
            while not (inst.status == "need_code" and "不对" in (inst._last_hint or "")):
                await asyncio.sleep(0.05)
            assert inst.submit_verify_code("4567")["ok"]
        ticker = asyncio.create_task(user())
        from wechatbot import auth
        creds = await asyncio.wait_for(auth.login(api, cred_path=tmp_path / "c.json", force=True,
                                                  on_qr_url=inst._on_qr_url, on_scanned=inst._on_scanned,
                                                  on_verify_code=inst._on_verify_code), 30)
        await ticker
        return creds
    creds = asyncio.run(run())
    assert creds.token == "tok"
    assert [c for c in seen_codes if c] == ["12", "4567"]


def test_submit_when_not_needed():
    i = Inst()
    assert not i.submit_verify_code("123")["ok"]
    i.status = "need_code"
    assert not i.submit_verify_code("abc")["ok"]

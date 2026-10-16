"""定时触发复用 Web 会话：忙时跳过，闲时启动并留下历史与流。"""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


def test_busy_session_is_not_started_twice():
    from lib.timer_agent import call_timer_agent
    with patch('routers.chat_router.REGISTRY.running', return_value=True), \
         patch('routers.chat_router.start_chat_turn') as start:
        assert '跳过' in asyncio.run(call_timer_agent('检查桌面', 'web-test'))
        start.assert_not_called()


def test_idle_timer_uses_same_persistent_web_turn():
    from lib.timer_agent import call_timer_agent
    from routers.chat_router import start_chat_turn
    session = {'messages': [], 'name': ''}
    wui = SimpleNamespace(load_session=Mock(return_value=session), save_session=Mock(),
                          _build_multimodal_content=Mock(return_value=('检查桌面', 0)),
                          MODEL='test', SERVER_URL='http://core')
    registry = Mock()
    registry.running.return_value = False
    async def run():
        with patch('routers.chat_router._wui', return_value=wui), \
             patch('routers.chat_router.REGISTRY', registry), \
             patch('routers.chat_router._producer', return_value='producer'):
            result = await call_timer_agent('检查桌面', 'web-test')
            assert '已启动后台轮次' in result
    asyncio.run(run())
    assert session['messages'][0]['content'] == '检查桌面'
    wui.save_session.assert_called_once()
    registry.start.assert_called_once_with('web-test', 'producer')


def test_web_turn_rejects_busy_before_writing_history():
    import pytest
    from fastapi import HTTPException
    from routers.chat_router import start_chat_turn
    with patch('routers.chat_router.REGISTRY.running', return_value=True), \
         patch('routers.chat_router._wui') as wui:
        with pytest.raises(HTTPException) as exc:
            start_chat_turn('web-test', '检查桌面')
        assert exc.value.status_code == 409
        wui.assert_not_called()


def test_agent_timer_without_target_inherits_current_session():
    from core.tools.handlers.timer import h_create_timer
    manager = Mock()
    manager.add_timer.return_value = {'id': 'timer-test', 'name': '巡检', 'type': 'cron', 'schedule': '*/2 * * * *'}
    with patch('core.tools.handlers.timer._tm', return_value=manager):
        result, _ = asyncio.run(h_create_timer('web-source', {
            'name': '巡检', 'type': 'cron', 'schedule': '*/2 * * * *',
            'action_type': 'agent', 'action_content': '检查桌面'}))
    assert '已创建' in result
    assert manager.add_timer.call_args.args[0]['action_target'] == 'web-source'

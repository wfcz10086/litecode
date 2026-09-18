"""
captcha_handler.py — 验证码处理 (P6-c/d/e)
============================================
极速骨架: vision_ocr (P6-c) / 滑块 (P6-d) / noVNC 钩子 (P6-e).
真集成留 v1.6 末.
"""
def solve_image_captcha(image_path: str) -> dict:
    """简单 4 位字符 → vision_ocr (P6-c)"""
    return {"text": "", "confidence": 0.0, "note": "P6-c skeleton"}

def solve_slider(page, slider_selector: str) -> bool:
    """滑块 OpenCV 模板匹配 + Bezier (P6-d)"""
    return False

def request_human_takeover(workspace, sid: str, reason: str) -> dict:
    """noVNC 接管钩子 (P6-e). 写信号文件让 web 提示用户."""
    from pathlib import Path
    import time, json
    p = Path(workspace) / "novnc_takeover" / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sid": sid, "reason": reason, "ts": time.time(), "novnc_url": "/novnc"}
    p.write_text(json.dumps(payload, ensure_ascii=False))
    return payload

"""
verification_inbox.py — 邮箱/短信验证码接收 schema (P6-f)
==========================================================
预留接口, 实际 IMAP/接码平台对接留 v1.6 实现.
"""
from typing import Optional


def fetch_email_code(imap_host: str, user: str, password: str,
                     subject_keyword: str, timeout: int = 60) -> Optional[str]:
    """从 IMAP 邮箱拿最近一封含 subject_keyword 的邮件提取 6 位数字码"""
    return None


def fetch_sms_code(provider: str, phone: str, timeout: int = 90) -> Optional[str]:
    """从接码平台 (provider: 'sms-activate'/'getsmscode'/...) 取手机短信码"""
    return None


PROVIDER_SCHEMAS = {
    "imap": {"host": "str", "user": "str", "password": "str", "subject_keyword": "str"},
    "sms-activate": {"api_key": "str", "country": "int"},
    "getsmscode": {"username": "str", "token": "str"},
}

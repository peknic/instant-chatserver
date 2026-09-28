import os

import resend

from .verifycode import issue_code, verify_code

DEFAULT_FROM = "noreply@animaker.cc.cd"


def send_code(email: str):
    """发码：校验配额后经 Resend 发送，返回 (ok, message, code|None)。"""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return False, "未配置 RESEND_API_KEY", None

    ok, msg, code = issue_code(email)
    if not ok:
        return False, msg, None

    resend.api_key = api_key
    params = {
        "from": os.getenv("CHAT_MAIL_FROM", DEFAULT_FROM),
        "to": [email],
        "subject": "Chatser 验证码",
        "html": f"""
        <div style="text-align:center;font-family:sans-serif;">
            <h2>Chatser 邮箱验证</h2>
            <p>你的验证码是：</p>
            <h1 style="color:#6366f1;letter-spacing:6px;">{code}</h1>
            <p style="color:#888;">5 分钟内有效，请勿泄露</p>
        </div>
        """,
    }

    try:
        resend.Emails.send(params)
        return True, "验证码已发送", code
    except Exception as e:  # noqa: BLE001
        return False, f"邮件发送失败: {e}", None


__all__ = ["send_code", "verify_code"]

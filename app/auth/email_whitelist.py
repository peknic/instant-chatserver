ALLOWED_DOMAINS = {
    "qq.com",
    "foxmail.com",
    "gmail.com",
    "googlemail.com",
    "163.com",
    "126.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "yahoo.com.cn",
    "yahoo.com.tw",
    "icloud.com",
    "me.com",
    "mac.com",
    "sina.com",
    "sohu.com",
    "139.com",
    "189.cn",
    "proton.me",
    "protonmail.com",
}


def is_allowed_email(email: str) -> bool:
    """仅允许白名单域名注册（域名小写匹配）。"""
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain in ALLOWED_DOMAINS


def normalize_email(email: str) -> str:
    """Gmail 风格 +别名归一化：abc+tag@domain -> abc@domain。"""
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}".strip().lower()


def is_dotted_email(email: str) -> bool:
    """本地部分含 1 个及以上点号的邮箱（如 z.u.l.ei.k@gmail.com），禁止注册。"""
    local = email.partition("@")[0]
    return "." in local

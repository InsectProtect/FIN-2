"""
Проверка initData, которую Telegram передаёт Mini App'у. Без этой проверки
кто угодно мог бы дёргать /api/* напрямую, подделав данные пользователя.
Алгоритм — официальный, из документации Telegram Web Apps.
"""
import hashlib
import hmac
import json
from urllib.parse import parse_qsl


def verify_init_data(init_data: str, bot_token: str) -> dict | None:
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    user_raw = parsed.get("user")
    if not user_raw:
        return None
    return json.loads(user_raw)

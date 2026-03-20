from __future__ import annotations

from typing import Any

import ccxt

from tradingbot.config import Settings


def build_exchange(settings: Settings, *, public_only: bool = False) -> ccxt.Exchange:
    klass = getattr(ccxt, settings.exchange_id, None)
    if klass is None:
        raise RuntimeError(
            f"지원하지 않거나 알 수 없는 EXCHANGE_ID 입니다: {settings.exchange_id!r}"
        )
    if public_only:
        return klass({"enableRateLimit": True})
    settings.require_credentials()
    params: dict[str, Any] = {
        "apiKey": settings.api_key,
        "secret": settings.api_secret,
        "enableRateLimit": True,
    }
    if settings.api_passphrase:
        params["password"] = settings.api_passphrase
    return klass(params)

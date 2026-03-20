from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def fetch_usd_rates() -> dict[str, float]:
    """open.er-api.com 기준: 각 키는 1 USD당 해당 통화 금액(예: KRW per USD)."""
    url = "https://open.er-api.com/v6/latest/USD"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "tradingbot/0.1 (+https://github.com/ccxt/ccxt)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data: dict[str, Any] = json.load(resp)
    except urllib.error.URLError as e:
        raise RuntimeError(f"환율 API 요청 실패: {e}") from e
    if data.get("result") != "success" or "rates" not in data:
        raise RuntimeError(f"환율 API 응답 형식 오류: {data!r}")
    rates = data["rates"]
    return {str(k).upper(): float(v) for k, v in rates.items()}

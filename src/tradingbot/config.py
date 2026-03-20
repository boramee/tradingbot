from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    exchange_id: str
    api_key: str
    api_secret: str
    api_passphrase: str | None
    dry_run: bool
    live_confirm: str | None

    @staticmethod
    def from_env() -> "Settings":
        raw_pass = os.getenv("API_PASSPHRASE")
        return Settings(
            exchange_id=os.getenv("EXCHANGE_ID", "binance").strip().lower(),
            api_key=os.getenv("API_KEY", "").strip(),
            api_secret=os.getenv("API_SECRET", "").strip(),
            api_passphrase=raw_pass.strip() if raw_pass else None,
            dry_run=_env_bool("DRY_RUN", True),
            live_confirm=os.getenv("TRADING_LIVE_CONFIRM"),
        )

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def require_credentials(self) -> None:
        if not self.has_credentials():
            raise RuntimeError(
                "API_KEY / API_SECRET 가 비어 있습니다. .env.example 을 참고해 .env 를 설정하세요."
            )

    def assert_live_order_allowed(self) -> None:
        if self.dry_run:
            raise RuntimeError(
                "실주문은 DRY_RUN=false 일 때만 가능합니다. 기본은 드라이런입니다."
            )
        if (self.live_confirm or "").strip() != "I_UNDERSTAND":
            raise RuntimeError(
                "실주문 전 TRADING_LIVE_CONFIRM=I_UNDERSTAND 를 .env 에 설정하세요."
            )

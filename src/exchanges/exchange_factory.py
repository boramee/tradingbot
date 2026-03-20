"""거래소 인스턴스 팩토리"""

import logging
from typing import Dict, List

from config.settings import AppConfig, ExchangeKeys
from .base_exchange import BaseExchange
from .upbit_exchange import UpbitExchange
from .ccxt_exchange import CcxtExchange, EXCHANGE_CONFIGS

logger = logging.getLogger(__name__)


def create_exchange(name: str, keys: ExchangeKeys) -> BaseExchange:
    """거래소 이름으로 클라이언트 인스턴스 생성"""
    name = name.lower()
    if name == "upbit":
        return UpbitExchange(keys)
    if name in EXCHANGE_CONFIGS:
        return CcxtExchange(name, keys)
    raise ValueError(f"지원하지 않는 거래소: {name}")


def create_all_exchanges(config: AppConfig) -> Dict[str, BaseExchange]:
    """설정에 있는 모든 거래소 클라이언트 생성 (API 키 없어도 시세 조회용으로 생성)"""
    exchanges: Dict[str, BaseExchange] = {}

    exchange_map = {
        "upbit": config.upbit,
        "bithumb": config.bithumb,
        "binance": config.binance,
        "bybit": config.bybit,
    }

    for name, keys in exchange_map.items():
        try:
            ex = create_exchange(name, keys)
            exchanges[name] = ex
            auth_status = "인증됨" if keys.is_valid else "시세조회 전용"
            logger.info("[%s] 거래소 연결 (%s)", name.upper(), auth_status)
        except Exception as e:
            logger.warning("[%s] 거래소 연결 실패: %s", name, e)

    return exchanges

"""공통 안전장치 - Kill Switch, 거래 기록, API 보호"""

from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class KillSwitch:
    """일일 손실 한도 초과 시 당일 매매 강제 중단.

    초기 자금 대비 N% 이상 손실나면 그날은 더 이상 매매하지 않음.
    재시작해도 킬스위치 상태가 유지되도록 파일에 저장.
    """

    _STATE_FILE = "logs/killswitch_state.json"

    def __init__(self, max_daily_loss_pct: float = 3.0, initial_capital: float = 0):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.initial_capital = initial_capital
        self._daily_pnl: float = 0.0
        self._date: str = ""
        self._killed: bool = False
        self._load_state()

    def record_trade(self, pnl_amount: float):
        self._reset_if_new_day()
        self._daily_pnl += pnl_amount

        if self.initial_capital > 0:
            loss_pct = abs(self._daily_pnl) / self.initial_capital * 100
        else:
            loss_pct = 0

        if self._daily_pnl < 0 and loss_pct >= self.max_daily_loss_pct:
            self._killed = True
            logger.warning("[KILL SWITCH] 일일 손실 %.1f%% → 당일 매매 중단", loss_pct)

        self._save_state()

    def is_killed(self) -> bool:
        self._reset_if_new_day()
        return self._killed

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    def _reset_if_new_day(self):
        today = date.today().isoformat()
        if self._date != today:
            if self._date and self._daily_pnl != 0:
                logger.info("[KILL SWITCH] 일일 PnL 리셋 (전일: %+.0f원)", self._daily_pnl)
            self._daily_pnl = 0.0
            self._killed = False
            self._date = today
            self._save_state()

    def _save_state(self):
        """킬스위치 상태를 파일에 저장 (재시작 시 복원)"""
        import json
        try:
            os.makedirs(os.path.dirname(self._STATE_FILE), exist_ok=True)
            with open(self._STATE_FILE, "w") as f:
                json.dump({
                    "date": self._date,
                    "daily_pnl": self._daily_pnl,
                    "killed": self._killed,
                }, f)
        except Exception:
            pass

    def _load_state(self):
        """파일에서 킬스위치 상태 복원"""
        import json
        try:
            if os.path.exists(self._STATE_FILE):
                with open(self._STATE_FILE, "r") as f:
                    state = json.load(f)
                saved_date = state.get("date", "")
                if saved_date == date.today().isoformat():
                    self._date = saved_date
                    self._daily_pnl = state.get("daily_pnl", 0.0)
                    self._killed = state.get("killed", False)
                    if self._killed:
                        logger.warning("[KILL SWITCH] 파일에서 복원 — 킬스위치 활성 (PnL: %+.0f원)",
                                       self._daily_pnl)
        except Exception:
            pass


class TradeLogger:
    """모든 거래를 CSV에 기록 (복기/백테스트용)"""

    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, "trades.csv")
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(self._path):
            with open(self._path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "datetime", "bot", "side", "symbol", "exchange",
                    "price", "quantity", "amount", "fee",
                    "pnl_pct", "pnl_amount", "reason",
                    "rsi", "macd_hist", "adx", "atr", "volume_ratio",
                ])

    def log(
        self,
        bot: str,
        side: str,
        symbol: str,
        exchange: str = "",
        price: float = 0,
        quantity: float = 0,
        amount: float = 0,
        fee: float = 0,
        pnl_pct: float = 0,
        pnl_amount: float = 0,
        reason: str = "",
        indicators: Optional[Dict] = None,
    ):
        ind = indicators or {}
        try:
            with open(self._path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    bot, side, symbol, exchange,
                    price, quantity, amount, fee,
                    "%.4f" % pnl_pct, "%.2f" % pnl_amount, reason,
                    "%.1f" % ind.get("rsi", 0),
                    "%.4f" % ind.get("macd_hist", 0),
                    "%.1f" % ind.get("adx", 0),
                    "%.1f" % ind.get("atr", 0),
                    "%.2f" % ind.get("vol_ratio", 0),
                ])
        except Exception as e:
            logger.debug("거래 로그 기록 실패: %s", e)


class APIGuard:
    """API 호출 속도 제한 + 네트워크 끊김 대응"""

    def __init__(self, calls_per_sec: float = 5, max_retries: int = 3):
        self.min_interval = 1.0 / calls_per_sec
        self.max_retries = max_retries
        self._last_call: float = 0
        self._consecutive_errors: int = 0
        self._backoff_until: float = 0

    def wait_if_needed(self):
        now = time.time()
        if now < self._backoff_until:
            wait = self._backoff_until - now
            logger.debug("[API] 백오프 대기: %.1f초", wait)
            time.sleep(wait)

        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()

    def on_success(self):
        self._consecutive_errors = 0

    def on_error(self, error: Exception):
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.max_retries:
            backoff = min(60, 2 ** self._consecutive_errors)
            self._backoff_until = time.time() + backoff
            logger.warning("[API] %d연속 에러 → %d초 백오프", self._consecutive_errors, backoff)

    @property
    def is_healthy(self) -> bool:
        return self._consecutive_errors < self.max_retries

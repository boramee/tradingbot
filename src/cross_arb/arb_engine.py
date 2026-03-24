"""양쪽 자금 배치형 거래소 간 재정거래 엔진

전략:
  업비트(KRW) + 바이낸스(USDT)에 자금을 미리 배치
  가격 차이(김프) 발생 시 동시에:
    - 싼 쪽에서 매수 + 비싼 쪽에서 매도
  코인 전송 없이 가격 차이만큼 수익

리밸런싱:
  한쪽에 코인이 쌓이면 텔레그램으로 전송 알림
"""

from __future__ import annotations

import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pyupbit
import ccxt

from src.monitor.fx_rate import FXRateProvider
from src.utils.telegram_bot import TelegramNotifier
from src.utils.daily_report import DailyReport

logger = logging.getLogger(__name__)


@dataclass
class ArbOpportunity:
    coin: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    buy_quote: str
    sell_quote: str
    spread_pct: float
    net_profit_pct: float
    fx_rate: float

    def summary(self) -> str:
        return (
            "%s | 매수:%s(%s %s) → 매도:%s(%s %s) | "
            "스프레드:%+.3f%% | 순수익:%+.3f%%"
            % (self.coin,
               self.buy_exchange, "{:,.0f}".format(self.buy_price) if self.buy_price > 100 else "%.4f" % self.buy_price, self.buy_quote,
               self.sell_exchange, "{:,.0f}".format(self.sell_price) if self.sell_price > 100 else "%.4f" % self.sell_price, self.sell_quote,
               self.spread_pct, self.net_profit_pct)
        )


@dataclass
class ArbTradeLog:
    timestamp: float
    coin: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    amount_usdt: float
    net_profit_usdt: float
    net_profit_pct: float


class CrossArbEngine:
    """양쪽 자금 배치형 재정거래"""

    UPBIT_FEE = 0.0005       # 0.05%
    BINANCE_FEE = 0.001      # 0.1%
    BINANCE_MAKER_FEE = 0.0001  # 0.01% (Maker 수수료)
    TOTAL_FEE_PCT = (UPBIT_FEE + BINANCE_FEE) * 100  # 0.15% (양쪽 Taker)

    # 전송 속도/수수료 기준 코인 순위 (리밸런싱용)
    TRANSFER_PRIORITY = {
        "XRP": {"speed": "3초", "fee_usdt": 0.1},
        "TRX": {"speed": "3초", "fee_usdt": 0.1},
        "SOL": {"speed": "5초", "fee_usdt": 0.01},
        "ETH": {"speed": "5분", "fee_usdt": 2.0},
        "BTC": {"speed": "30분", "fee_usdt": 5.0},
    }

    # v6: 동적 최소 수익 안전 마진 배수
    SAFETY_MARGIN = 1.5

    def __init__(
        self,
        upbit_access: str = "",
        upbit_secret: str = "",
        binance_access: str = "",
        binance_secret: str = "",
        coins: str = "BTC,ETH,XRP,SOL,TRX",
        min_profit_pct: float = 0.3,
        max_trade_krw: int = 100_000,
        slippage_pct: float = 0.1,
        poll_interval: int = 5,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        live: bool = False,
    ):
        self.coins = [c.strip().upper() for c in coins.split(",")]
        self._base_min_profit_pct = min_profit_pct  # 사용자 설정 기본값
        self.min_profit_pct = min_profit_pct
        self.max_trade_krw = max_trade_krw
        self.slippage_pct = slippage_pct
        self.poll_interval = poll_interval
        self._use_maker_strategy = True  # v6: Maker-Maker 전략 활성화

        # 업비트
        self._upbit: Optional[pyupbit.Upbit] = None
        if upbit_access and upbit_secret:
            self._upbit = pyupbit.Upbit(upbit_access, upbit_secret)

        # 바이낸스
        self._binance: Optional[ccxt.binance] = None
        opts = {"enableRateLimit": True, "timeout": 10000}
        if binance_access and binance_secret:
            opts["apiKey"] = binance_access
            opts["secret"] = binance_secret
        self._binance = ccxt.binance(opts)

        self.fx = FXRateProvider()
        self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        self._trade_logger = __import__("src.utils.safety", fromlist=["TradeLogger"]).TradeLogger()
        self._kill_switch = __import__("src.utils.safety", fromlist=["KillSwitch"]).KillSwitch(max_daily_loss_pct=5.0)
        self.running = False
        self._simulation_only = not live       # 기본: 모의 모드 강제
        self.trade_logs: List[ArbTradeLog] = []
        self._daily_pnl_usdt = 0.0
        self._daily_trades = 0
        self._max_daily_trades = 20
        self._last_trade_time: Dict[str, float] = {}
        self._trade_cooldown = 120  # 같은 코인 2분 쿨다운
        self._rebalance_alerted: Dict[str, bool] = {}
        self._daily_report = DailyReport()
        self._last_report_date: str = ""
        self._pending_transfers: Dict[str, Dict] = {}  # 입출금 대기 추적

    # ── 가격 조회 ──

    def _get_prices(self, coin: str) -> Optional[Dict]:
        """업비트(KRW) + 바이낸스(USDT) 가격 동시 조회"""
        try:
            upbit_ob = pyupbit.get_orderbook("KRW-%s" % coin)
            if not upbit_ob:
                return None
            ob = upbit_ob[0] if isinstance(upbit_ob, list) else upbit_ob
            units = ob.get("orderbook_units", [])
            if not units:
                return None
            upbit_bid = float(units[0]["bid_price"])
            upbit_ask = float(units[0]["ask_price"])
        except Exception as e:
            logger.debug("[업비트] %s 조회 실패: %s", coin, e)
            return None

        try:
            bn_ob = self._binance.fetch_order_book("%s/USDT" % coin, limit=5)
            if not bn_ob["bids"] or not bn_ob["asks"]:
                return None
            bn_bid = float(bn_ob["bids"][0][0])
            bn_ask = float(bn_ob["asks"][0][0])
        except Exception as e:
            logger.debug("[바이낸스] %s 조회 실패: %s", coin, e)
            return None

        fx_rate = self.fx.get_krw_per_usdt()

        return {
            "coin": coin,
            "upbit_bid": upbit_bid,
            "upbit_ask": upbit_ask,
            "binance_bid": bn_bid,
            "binance_ask": bn_ask,
            "fx_rate": fx_rate,
            "upbit_bid_usdt": upbit_bid / fx_rate,
            "upbit_ask_usdt": upbit_ask / fx_rate,
        }

    def _calc_dynamic_min_profit(self, coin: str, use_maker: bool = False) -> float:
        """v6: 동적 최소 수익 = (수수료 + 슬리피지 + 전송비용) × 안전마진

        네트워크 전송 비용을 포함하고 안전 마진을 곱하여
        실제 역마진 위험을 방지.
        """
        # 수수료: Maker 전략이면 바이낸스 쪽 Maker 수수료 적용
        if use_maker:
            fee_pct = (self.UPBIT_FEE + self.BINANCE_MAKER_FEE) * 100
        else:
            fee_pct = self.TOTAL_FEE_PCT

        # 전송 비용 (USDT 기준) → 거래 금액 대비 %로 환산
        transfer_info = self.TRANSFER_PRIORITY.get(coin)
        transfer_cost_pct = 0.0
        if transfer_info:
            trade_usdt = self.max_trade_krw / self.fx.get_krw_per_usdt()
            if trade_usdt > 0:
                transfer_cost_pct = (transfer_info["fee_usdt"] / trade_usdt) * 100

        total_cost = fee_pct + self.slippage_pct + transfer_cost_pct
        dynamic_min = total_cost * self.SAFETY_MARGIN

        # 사용자 설정 기본값과 비교하여 더 높은 쪽 적용
        return max(dynamic_min, self._base_min_profit_pct)

    def _find_opportunity(self, prices: Dict) -> Optional[ArbOpportunity]:
        """v6: 양방향 재정거래 기회 탐지 — 동적 최소수익 + Maker 전략"""
        coin = prices["coin"]
        fx = prices["fx_rate"]

        # 동적 최소 수익 계산
        use_maker = self._use_maker_strategy
        dynamic_min = self._calc_dynamic_min_profit(coin, use_maker)

        # 수수료 계산 (Maker 전략 적용 여부)
        if use_maker:
            effective_fee = (self.UPBIT_FEE + self.BINANCE_MAKER_FEE) * 100
        else:
            effective_fee = self.TOTAL_FEE_PCT

        # 방향 1: 바이낸스에서 매수(ask) → 업비트에서 매도(bid)
        bn_ask_krw = prices["binance_ask"] * fx
        spread1_pct = (prices["upbit_bid"] - bn_ask_krw) / bn_ask_krw * 100
        net1 = spread1_pct - effective_fee - self.slippage_pct

        # 방향 2: 업비트에서 매수(ask) → 바이낸스에서 매도(bid)
        bn_bid_krw = prices["binance_bid"] * fx
        spread2_pct = (bn_bid_krw - prices["upbit_ask"]) / prices["upbit_ask"] * 100
        net2 = spread2_pct - effective_fee - self.slippage_pct

        # 더 수익이 높은 방향 선택 (동적 최소 수익 적용)
        if net1 > net2 and net1 >= dynamic_min:
            return ArbOpportunity(
                coin=coin, buy_exchange="binance", sell_exchange="upbit",
                buy_price=prices["binance_ask"], sell_price=prices["upbit_bid"],
                buy_quote="USDT", sell_quote="KRW",
                spread_pct=spread1_pct, net_profit_pct=net1, fx_rate=fx,
            )

        if net2 > net1 and net2 >= dynamic_min:
            return ArbOpportunity(
                coin=coin, buy_exchange="upbit", sell_exchange="binance",
                buy_price=prices["upbit_ask"], sell_price=prices["binance_bid"],
                buy_quote="KRW", sell_quote="USDT",
                spread_pct=spread2_pct, net_profit_pct=net2, fx_rate=fx,
            )

        return None

    def _check_orderbook_depth(self, coin: str, side: str, amount_usdt: float) -> bool:
        """호가창 5단계 이내에서 물량 소화 가능한지 확인"""
        try:
            if side == "binance_buy":
                ob = self._binance.fetch_order_book("%s/USDT" % coin, limit=5)
                asks = ob.get("asks", [])
                total = sum(float(a[0]) * float(a[1]) for a in asks[:5])
                return total >= amount_usdt

            elif side == "binance_sell":
                ob = self._binance.fetch_order_book("%s/USDT" % coin, limit=5)
                bids = ob.get("bids", [])
                total = sum(float(b[0]) * float(b[1]) for b in bids[:5])
                return total >= amount_usdt

            elif side == "upbit_buy":
                ob = pyupbit.get_orderbook("KRW-%s" % coin)
                if not ob:
                    return False
                data = ob[0] if isinstance(ob, list) else ob
                units = data.get("orderbook_units", [])[:5]
                total = sum(float(u["ask_price"]) * float(u["ask_size"]) for u in units)
                return total >= amount_usdt * self.fx.get_krw_per_usdt()

            elif side == "upbit_sell":
                ob = pyupbit.get_orderbook("KRW-%s" % coin)
                if not ob:
                    return False
                data = ob[0] if isinstance(ob, list) else ob
                units = data.get("orderbook_units", [])[:5]
                total = sum(float(u["bid_price"]) * float(u["bid_size"]) for u in units)
                return total >= amount_usdt * self.fx.get_krw_per_usdt()

        except Exception as e:
            logger.debug("호가 깊이 확인 실패: %s", e)
        return True  # 조회 실패 시 일단 진행

    # ── 매매 실행 ──

    def _execute(self, opp: ArbOpportunity) -> bool:
        """동시 매수/매도 실행 (simulation_only=True이면 항상 시뮬레이션)"""
        if self._kill_switch.is_killed():
            return False

        coin = opp.coin
        trade_usdt = min(self.max_trade_krw / opp.fx_rate, 1000)

        # 호가 깊이 체크
        buy_side = "%s_buy" % opp.buy_exchange
        sell_side = "%s_sell" % opp.sell_exchange
        if not self._check_orderbook_depth(coin, buy_side, trade_usdt):
            logger.info("[호가부족] %s 매수 호가 5단계 물량 부족", opp.buy_exchange)
            return False
        if not self._check_orderbook_depth(coin, sell_side, trade_usdt):
            logger.info("[호가부족] %s 매도 호가 5단계 물량 부족", opp.sell_exchange)
            return False

        # 모의 모드 강제: API 키가 있어도 시뮬레이션만 실행
        if self._simulation_only:
            if opp.buy_exchange == "binance":
                trade_krw = min(self.max_trade_krw, 1_000_000)
                trade_usdt = trade_krw / opp.fx_rate
                coin_qty = trade_usdt / opp.buy_price
                logger.info("[모의] %s 바이낸스 매수 %.6f + 업비트 매도 | 순수익: %+.3f%%",
                            coin, coin_qty, opp.net_profit_pct)
            else:
                trade_krw = min(self.max_trade_krw, 1_000_000)
                trade_usdt = trade_krw / opp.fx_rate
                logger.info("[모의] %s 업비트 매수 %s원 + 바이낸스 매도 | 순수익: %+.3f%%",
                            coin, "{:,}".format(trade_krw), opp.net_profit_pct)
            self._record_trade(opp, trade_usdt)
            return True

        if opp.buy_exchange == "binance":
            trade_krw = min(self.max_trade_krw, self._get_upbit_coin_value(coin))
            trade_usdt = trade_krw / opp.fx_rate
            coin_qty = trade_usdt / opp.buy_price

            if not self._upbit:
                logger.info("[시뮬] %s 바이낸스 매수 %.6f + 업비트 매도 | 순수익: %+.3f%%",
                            coin, coin_qty, opp.net_profit_pct)
                self._record_trade(opp, trade_usdt)
                return True

            # 동시 주문
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_buy = ex.submit(self._binance_buy, coin, coin_qty)
                f_sell = ex.submit(self._upbit_sell, coin)
                buy_ok = f_buy.result(timeout=15)
                sell_ok = f_sell.result(timeout=15)

        else:
            trade_krw = min(self.max_trade_krw, self._get_upbit_krw())
            trade_usdt = trade_krw / opp.fx_rate
            coin_qty = trade_usdt / (opp.sell_price if opp.sell_price > 0 else 1)

            if not self._upbit:
                logger.info("[시뮬] %s 업비트 매수 %s원 + 바이낸스 매도 | 순수익: %+.3f%%",
                            coin, "{:,}".format(trade_krw), opp.net_profit_pct)
                self._record_trade(opp, trade_usdt)
                return True

            with ThreadPoolExecutor(max_workers=2) as ex:
                f_buy = ex.submit(self._upbit_buy, coin, trade_krw)
                f_sell = ex.submit(self._binance_sell, coin, coin_qty)
                buy_ok = f_buy.result(timeout=15)
                sell_ok = f_sell.result(timeout=15)

        if buy_ok and sell_ok:
            self._record_trade(opp, trade_usdt)
            logger.info("[체결] %s", opp.summary())
            return True
        else:
            if buy_ok != sell_ok:
                logger.critical("[편체결] %s 매수:%s 매도:%s - 수동 확인 필요!",
                                coin, buy_ok, sell_ok)
                self.telegram.notify_error(
                    "편체결 발생!\n코인: %s\n매수(%s): %s\n매도(%s): %s"
                    % (coin, opp.buy_exchange, buy_ok, opp.sell_exchange, sell_ok))
            return False

    def _record_trade(self, opp: ArbOpportunity, trade_usdt: float):
        profit_usdt = trade_usdt * (opp.net_profit_pct / 100)
        profit_krw = profit_usdt * opp.fx_rate
        self._daily_pnl_usdt += profit_usdt
        self._daily_trades += 1
        self._last_trade_time[opp.coin] = time.time()
        self.trade_logs.append(ArbTradeLog(
            time.time(), opp.coin, opp.buy_exchange, opp.sell_exchange,
            opp.buy_price, opp.sell_price, trade_usdt, profit_usdt, opp.net_profit_pct,
        ))
        self._kill_switch.record_trade(profit_krw)
        self._trade_logger.log(
            bot="cross_arb", side="ARB", symbol=opp.coin,
            exchange="%s→%s" % (opp.buy_exchange, opp.sell_exchange),
            price=opp.buy_price, amount=trade_usdt,
            pnl_pct=opp.net_profit_pct, pnl_amount=profit_krw,
            reason="스프레드:%.3f%%" % opp.spread_pct,
        )
        self.telegram.notify_arbitrage(
            opp.coin, opp.buy_exchange, opp.sell_exchange,
            opp.spread_pct, opp.net_profit_pct)

    # ── 거래소 주문 ──

    def _upbit_buy(self, coin: str, krw: int) -> bool:
        try:
            r = self._upbit.buy_market_order("KRW-%s" % coin, krw)
            return r is not None and "error" not in r
        except Exception as e:
            logger.error("[업비트] %s 매수 실패: %s", coin, e)
            return False

    def _upbit_sell(self, coin: str) -> bool:
        try:
            bal = self._upbit.get_balance(coin)
            if not bal or float(bal) <= 0:
                return False
            r = self._upbit.sell_market_order("KRW-%s" % coin, float(bal))
            return r is not None and "error" not in r
        except Exception as e:
            logger.error("[업비트] %s 매도 실패: %s", coin, e)
            return False

    def _binance_buy(self, coin: str, qty: float) -> bool:
        try:
            r = self._binance.create_market_buy_order("%s/USDT" % coin, qty)
            return r is not None
        except Exception as e:
            logger.error("[바이낸스] %s 매수 실패: %s", coin, e)
            return False

    def _binance_sell(self, coin: str, qty: float) -> bool:
        try:
            r = self._binance.create_market_sell_order("%s/USDT" % coin, qty)
            return r is not None
        except Exception as e:
            logger.error("[바이낸스] %s 매도 실패: %s", coin, e)
            return False

    # ── 잔고 조회 ──

    def _get_upbit_krw(self) -> float:
        if not self._upbit:
            return 1_000_000
        try:
            return float(self._upbit.get_balance("KRW") or 0)
        except Exception:
            return 0

    def _get_upbit_coin_value(self, coin: str) -> float:
        if not self._upbit:
            return 1_000_000
        try:
            bal = float(self._upbit.get_balance(coin) or 0)
            price = float(pyupbit.get_current_price("KRW-%s" % coin) or 0)
            return bal * price
        except Exception:
            return 0

    def _check_rebalance(self):
        """잔고 불균형 감지 → 전송 추천 코인 포함 텔레그램 알림"""
        if not self._upbit:
            return
        try:
            krw = self._get_upbit_krw()
            bn_bal = self._binance.fetch_balance()
            usdt = float(bn_bal.get("free", {}).get("USDT", 0))
            fx = self.fx.get_krw_per_usdt()

            krw_ratio = krw / (krw + usdt * fx) * 100 if (krw + usdt * fx) > 0 else 50

            # 전송 추천 코인 (수수료 저렴한 순)
            recommend = "추천 전송 코인: XRP(0.1$,3초) > TRX(0.1$,3초) > SOL(0.01$,5초)"

            if krw_ratio < 20 and not self._rebalance_alerted.get("krw_low"):
                if self._pending_transfers:
                    pending = ", ".join(self._pending_transfers.keys())
                    logger.info("[리밸런싱] 전송 대기 중: %s → 알림 스킵", pending)
                    return
                self.telegram.send(
                    "<b>⚠️ 리밸런싱 필요</b>\n"
                    "업비트 KRW 부족 (%.0f%%)\n"
                    "업비트: %s원\n바이낸스: %s USDT\n"
                    "→ 바이낸스→업비트 코인 전송 필요\n%s"
                    % (krw_ratio, "{:,}".format(int(krw)), "{:,.0f}".format(usdt), recommend))
                self._rebalance_alerted["krw_low"] = True

            elif krw_ratio > 80 and not self._rebalance_alerted.get("usdt_low"):
                if self._pending_transfers:
                    return
                self.telegram.send(
                    "<b>⚠️ 리밸런싱 필요</b>\n"
                    "바이낸스 USDT 부족 (KRW %.0f%%)\n"
                    "업비트: %s원\n바이낸스: %s USDT\n"
                    "→ 업비트→바이낸스 코인 전송 필요\n%s"
                    % (krw_ratio, "{:,}".format(int(krw)), "{:,.0f}".format(usdt), recommend))
                self._rebalance_alerted["usdt_low"] = True

            elif 30 <= krw_ratio <= 70:
                self._rebalance_alerted.clear()

        except Exception as e:
            logger.debug("리밸런싱 체크 실패: %s", e)

    # ── 입출금 대기 추적 ──

    def _check_pending_transfers(self):
        """입출금 대기 중인 자산이 있으면 리밸런싱 알림 억제"""
        if not self._upbit or not self._pending_transfers:
            return

        completed = []
        for coin, info in self._pending_transfers.items():
            elapsed = time.time() - info.get("time", 0)
            if elapsed > 3600:  # 1시간 초과 → 완료 또는 실패로 간주
                completed.append(coin)
                self.telegram.send(
                    "<b>📦 전송 추적 해제</b>\n%s 전송 후 1시간 경과 → 수동 확인 필요" % coin)

        for coin in completed:
            del self._pending_transfers[coin]

    def _record_pending_transfer(self, coin: str, direction: str, amount: float):
        """리밸런싱 전송 기록 (중복 전송 방지)"""
        if coin in self._pending_transfers:
            logger.info("[전송 중복 방지] %s 이미 전송 대기 중", coin)
            return False
        self._pending_transfers[coin] = {
            "direction": direction, "amount": amount, "time": time.time(),
        }
        return True

    # ── 메인 사이클 ──

    def run_once(self):
        now = time.time()

        if self._kill_switch.is_killed():
            return
        if self._daily_trades >= self._max_daily_trades:
            return

        self._check_pending_transfers()

        # 모든 코인의 스프레드를 수집하고, 가장 큰 것부터 실행
        opportunities: List[ArbOpportunity] = []

        for coin in self.coins:
            last = self._last_trade_time.get(coin, 0)
            if now - last < self._trade_cooldown:
                continue

            prices = self._get_prices(coin)
            if not prices:
                continue

            opp = self._find_opportunity(prices)
            if opp:
                opportunities.append(opp)
            else:
                kimchi = (prices["upbit_bid_usdt"] - prices["binance_ask"]) / prices["binance_ask"] * 100
                logger.debug("[%s] 김프: %+.2f%% | 순수익 부족", coin, kimchi)

        if not opportunities:
            return

        # 순수익이 가장 큰 코인 선택
        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        best = opportunities[0]
        logger.info("[최적코인] %s (순수익: %+.3f%%) — 후보: %s",
                     best.coin,
                     best.net_profit_pct,
                     ", ".join("%s(%+.2f%%)" % (o.coin, o.net_profit_pct) for o in opportunities))
        self._execute(best)

    def start(self):
        self.running = True

        def _stop(signum, frame):
            self.running = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        if self._simulation_only:
            mode = "모의거래 (실거래 비활성화)"
        elif self._upbit:
            mode = "실거래"
        else:
            mode = "시뮬레이션 (API키 없음)"
        logger.info("=" * 60)
        logger.info("  거래소 간 재정거래 봇 시작")
        logger.info("  코인: %s", ", ".join(self.coins))
        logger.info("  모드: %s", mode)
        maker_tag = " [Maker전략]" if self._use_maker_strategy else ""
        logger.info("  최소 순수익: %.2f%% 기본 (동적 = 비용×%.1f 안전마진)%s",
                     self._base_min_profit_pct, self.SAFETY_MARGIN, maker_tag)
        logger.info("  수수료: %.2f%% + 슬리피지 %.2f%%", self.TOTAL_FEE_PCT, self.slippage_pct)
        logger.info("  1회 최대: %s원", "{:,}".format(self.max_trade_krw))
        logger.info("  주기: %d초 | 코인별 쿨다운: %d초", self.poll_interval, self._trade_cooldown)
        logger.info("=" * 60)
        self.telegram.notify_start(
            ", ".join(self.coins), "재정거래 (%s)" % mode, mode)

        rebalance_check = 0
        while self.running:
            try:
                self.run_once()

                rebalance_check += self.poll_interval
                if rebalance_check >= 300:
                    self._check_rebalance()
                    rebalance_check = 0

                self._send_daily_report_if_needed()

            except Exception as e:
                logger.error("사이클 오류: %s", e, exc_info=True)

            if self.running:
                for _ in range(self.poll_interval):
                    if not self.running:
                        break
                    time.sleep(1)

        logger.info("봇 종료 (일일 PnL: %+.4f USDT, 거래: %d건)",
                     self._daily_pnl_usdt, self._daily_trades)

    def _send_daily_report_if_needed(self):
        import datetime as dt
        today = dt.date.today().isoformat()
        if self._last_report_date == today:
            return
        if not self._last_report_date:
            self._last_report_date = today
            return
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        report = self._daily_report.generate(yesterday)
        self.telegram.send(report)
        logger.info("[일일리포트] %s 전송 완료", yesterday)
        self._last_report_date = today

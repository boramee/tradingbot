#!/usr/bin/env python3
"""스윙 눌림목 백테스트

전략:
  1. 전일 +2~15% 상승 종목 = 관심종목
  2. 다음날~3일 내 전일종가-3% 또는 5MA 도달 시 매수
  3. 손절 -3% / 익절 +5% / 트레일링 -2% (고점 대비) / 5일 보유제한

사용법:
  python3 run_swing_backtest.py
  python3 run_swing_backtest.py --codes 005930,000660,035720
  python3 run_swing_backtest.py --days 365 --capital 1000000
"""

from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd


# ── 데이터 ──

def fetch_data(code: str, days: int = 500) -> pd.DataFrame | None:
    """pykrx로 일봉 데이터 조회"""
    try:
        from pykrx import stock as pykrx_stock
        now = datetime.now()
        for offset in [0, 7, 30, 90, 180, 365, 730]:
            end_dt = now - timedelta(days=offset)
            start_dt = end_dt - timedelta(days=days)
            df = pykrx_stock.get_market_ohlcv_by_date(
                start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"), code)
            if df is not None and len(df) >= 60:
                df = df.iloc[:, :5]
                df.columns = ["open", "high", "low", "close", "volume"]
                name = pykrx_stock.get_market_ticker_name(code)
                print(f"  {code} {name}: {len(df)}일 로드 (기준일: {end_dt.strftime('%Y-%m-%d')})")
                return df
    except Exception as e:
        print(f"  {code} 데이터 로드 실패: {e}")
    return None


def get_top_stocks(n: int = 30) -> list:
    """코스피+코스닥 시가총액 상위 종목 코드 (백테스트용 유니버스)"""
    try:
        from pykrx import stock as pykrx_stock
        now = datetime.now()
        for offset in [0, 7, 30]:
            dt = (now - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                kospi = pykrx_stock.get_market_cap_by_ticker(dt, market="KOSPI")
                kosdaq = pykrx_stock.get_market_cap_by_ticker(dt, market="KOSDAQ")
                if kospi is not None and len(kospi) > 0:
                    combined = pd.concat([kospi, kosdaq]).sort_values("시가총액", ascending=False)
                    codes = combined.index.tolist()[:n]
                    print(f"유니버스: 시총 상위 {len(codes)}종목 (기준일: {dt})")
                    return codes
            except Exception:
                continue
    except ImportError:
        pass
    # 폴백: 대표 종목
    return ["005930", "000660", "035720", "051910", "006400",
            "005380", "003670", "105560", "055550", "034730"]


# ── 백테스트 로직 ──

@dataclass
class Trade:
    code: str
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0
    pnl_pct: float = 0
    reason_out: str = ""
    hold_days: int = 0
    highest: float = 0


@dataclass
class SwingBacktestResult:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    avg_win: float = 0
    avg_loss: float = 0
    profit_factor: float = 0
    total_return_pct: float = 0
    max_drawdown: float = 0
    avg_hold_days: float = 0
    trades: List[Trade] = field(default_factory=list)


FEE = 0.001  # 편도 0.1% (수수료+세금)


def run_backtest(
    codes: list,
    days: int = 365,
    stop_loss: float = 3.0,
    take_profit: float = 5.0,
    trailing: float = 2.0,
    max_hold: int = 5,
    initial_capital: float = 1_000_000,
) -> SwingBacktestResult:
    """스윙 눌림목 백테스트 실행"""

    print(f"\n{'='*60}")
    print(f"  스윙 눌림목 백테스트")
    print(f"  종목: {len(codes)}개 | 기간: {days}일")
    print(f"  손절: -{stop_loss}% | 익절: +{take_profit}% | 트레일링: -{trailing}%")
    print(f"  보유제한: {max_hold}일 | 수수료: {FEE*100:.1f}%/편도")
    print(f"  자본금: {initial_capital:,.0f}원")
    print(f"{'='*60}\n")

    # 모든 종목 데이터 로드
    all_data = {}
    for code in codes:
        df = fetch_data(code, days + 60)  # MA20 계산 여유분
        if df is not None and len(df) >= 40:
            all_data[code] = df

    if not all_data:
        print("데이터 없음!")
        return SwingBacktestResult()

    trades: List[Trade] = []
    capital = initial_capital
    peak_capital = capital
    max_dd = 0

    # 날짜별 시뮬레이션
    # 모든 종목의 공통 날짜 범위
    all_dates = set()
    for df in all_data.values():
        all_dates.update(df.index.strftime("%Y-%m-%d").tolist())
    sorted_dates = sorted(all_dates)

    # 최소 20일 이후부터 시작 (MA 계산용)
    if len(sorted_dates) < 25:
        print("날짜 부족!")
        return SwingBacktestResult()

    watchlist = {}  # code -> {close, ma5, pullback_target, added_idx, change_pct}
    active_trades = {}  # code -> Trade

    for i, date_str in enumerate(sorted_dates[20:], start=20):

        # ── 1. 보유 종목 매도 체크 ──
        for code in list(active_trades.keys()):
            if code not in all_data:
                continue
            df = all_data[code]
            if date_str not in df.index.strftime("%Y-%m-%d").values:
                continue

            idx = df.index.get_loc(df.index[df.index.strftime("%Y-%m-%d") == date_str][0])
            row = df.iloc[idx]
            trade = active_trades[code]

            # 당일 시가~저가~고가~종가 중 손절/익절 체크 (보수적: 저가로 손절, 고가로 익절)
            low = float(row["low"])
            high = float(row["high"])
            close = float(row["close"])
            trade.hold_days += 1
            trade.highest = max(trade.highest, high)

            sold = False
            sell_price = 0
            reason = ""

            # 손절 체크 (저가 기준)
            sl_price = trade.entry_price * (1 - stop_loss / 100)
            if low <= sl_price:
                sell_price = sl_price
                reason = f"손절 (-{stop_loss}%)"
                sold = True

            # 트레일링 체크 (고점 대비)
            if not sold and trade.highest > 0:
                trail_price = trade.highest * (1 - trailing / 100)
                pnl_at_trail = (trail_price - trade.entry_price) / trade.entry_price * 100
                if pnl_at_trail > 0 and low <= trail_price:
                    sell_price = trail_price
                    reason = f"트레일링 (고점{trade.highest:,.0f}→{trail_price:,.0f})"
                    sold = True

            # 익절 체크 (고가 기준)
            if not sold:
                tp_price = trade.entry_price * (1 + take_profit / 100)
                if high >= tp_price:
                    sell_price = tp_price
                    reason = f"익절 (+{take_profit}%)"
                    sold = True

            # 5일 보유 제한
            if not sold and trade.hold_days >= max_hold:
                sell_price = close
                reason = f"보유제한 {max_hold}일"
                sold = True

            if sold:
                pnl = (sell_price - trade.entry_price) / trade.entry_price * 100 - FEE * 100 * 2
                trade.exit_date = date_str
                trade.exit_price = sell_price
                trade.pnl_pct = pnl
                trade.reason_out = reason
                trades.append(trade)
                capital *= (1 + pnl / 100)
                del active_trades[code]

                # 최대 낙폭
                peak_capital = max(peak_capital, capital)
                dd = (peak_capital - capital) / peak_capital * 100
                max_dd = max(max_dd, dd)

        # ── 2. 관심종목 갱신 (전일 +2~15% 상승 종목) ──
        for code, df in all_data.items():
            if date_str not in df.index.strftime("%Y-%m-%d").values:
                continue
            idx = df.index.get_loc(df.index[df.index.strftime("%Y-%m-%d") == date_str][0])
            if idx < 20:
                continue

            row = df.iloc[idx]
            prev = df.iloc[idx - 1]
            prev_close = float(prev["close"])
            cur_close = float(row["close"])

            if prev_close <= 0:
                continue
            change_pct = (cur_close - prev_close) / prev_close * 100

            # +2~15% 상승 종목 = 관심종목
            if 2.0 <= change_pct <= 15.0:
                # MA5 계산
                ma5 = float(df["close"].iloc[idx-4:idx+1].mean())
                pullback = max(cur_close * 0.97, ma5) if ma5 > 0 else cur_close * 0.97

                watchlist[code] = {
                    "close": cur_close,
                    "ma5": ma5,
                    "pullback_target": pullback,
                    "added_date": date_str,
                    "added_idx": i,
                    "change_pct": change_pct,
                }

        # ── 3. 눌림목 매수 체크 ──
        if len(active_trades) >= 3:
            continue

        for code, watch in list(watchlist.items()):
            if code in active_trades:
                continue
            # 당일 추가된 건 스킵 (다음날부터)
            if watch["added_date"] == date_str:
                continue
            # 3일 만료
            if i - watch["added_idx"] > 3:
                del watchlist[code]
                continue

            if code not in all_data:
                continue
            df = all_data[code]
            if date_str not in df.index.strftime("%Y-%m-%d").values:
                continue

            idx = df.index.get_loc(df.index[df.index.strftime("%Y-%m-%d") == date_str][0])
            row = df.iloc[idx]
            cur_low = float(row["low"])
            cur_close = float(row["close"])
            cur_open = float(row["open"])

            # 눌림목 도달: 장중 저가가 목표가 이하
            if cur_low <= watch["pullback_target"]:
                # 양봉 확인 (종가 > 시가)
                if cur_close <= cur_open:
                    continue

                entry_price = watch["pullback_target"]
                trade = Trade(
                    code=code,
                    entry_date=date_str,
                    entry_price=entry_price,
                    highest=float(row["high"]),
                )
                active_trades[code] = trade

                if len(active_trades) >= 3:
                    break

    # 미청산 포지션 정리
    for code, trade in active_trades.items():
        if code in all_data:
            df = all_data[code]
            last_close = float(df["close"].iloc[-1])
            pnl = (last_close - trade.entry_price) / trade.entry_price * 100 - FEE * 100 * 2
            trade.exit_date = sorted_dates[-1]
            trade.exit_price = last_close
            trade.pnl_pct = pnl
            trade.reason_out = "백테스트 종료"
            trades.append(trade)
            capital *= (1 + pnl / 100)

    # ── 결과 집계 ──
    result = SwingBacktestResult(trades=trades)
    result.total_trades = len(trades)
    if not trades:
        return result

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    result.wins = len(wins)
    result.losses = len(losses)
    result.win_rate = len(wins) / len(trades) * 100
    result.avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
    result.avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
    total_win = sum(t.pnl_pct for t in wins)
    total_loss = abs(sum(t.pnl_pct for t in losses))
    result.profit_factor = total_win / total_loss if total_loss > 0 else float("inf")
    result.total_return_pct = (capital - initial_capital) / initial_capital * 100
    result.max_drawdown = max_dd
    result.avg_hold_days = np.mean([t.hold_days for t in trades])

    return result


def print_result(r: SwingBacktestResult):
    """결과 출력"""
    print(f"\n{'='*60}")
    print(f"  백테스트 결과")
    print(f"{'='*60}")
    print(f"  총 거래: {r.total_trades}회")
    print(f"  승/패: {r.wins}승 {r.losses}패")
    print(f"  승률: {r.win_rate:.1f}%")
    print(f"  평균 수익: +{r.avg_win:.2f}% | 평균 손실: {r.avg_loss:.2f}%")
    print(f"  Profit Factor: {r.profit_factor:.2f}")
    print(f"  총 수익률: {r.total_return_pct:+.2f}%")
    print(f"  최대 낙폭(MDD): -{r.max_drawdown:.2f}%")
    print(f"  평균 보유일: {r.avg_hold_days:.1f}일")
    print(f"{'='*60}")

    if r.trades:
        print(f"\n  최근 거래 (최대 20건):")
        print(f"  {'날짜':>12} {'종목':>8} {'매수':>10} {'매도':>10} {'수익률':>8} {'보유':>4} {'사유'}")
        print(f"  {'-'*70}")
        for t in r.trades[-20:]:
            print(f"  {t.entry_date:>12} {t.code:>8} {t.entry_price:>10,.0f} "
                  f"{t.exit_price:>10,.0f} {t.pnl_pct:>+7.2f}% {t.hold_days:>3}일 {t.reason_out}")

    # 월별 수익률
    if r.trades:
        print(f"\n  월별 수익률:")
        monthly = {}
        for t in r.trades:
            month = t.exit_date[:7]
            monthly.setdefault(month, []).append(t.pnl_pct)
        for month in sorted(monthly):
            pnls = monthly[month]
            total = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            print(f"  {month}: {total:+6.2f}% ({len(pnls)}건, {wins}승)")


def main():
    parser = argparse.ArgumentParser(description="스윙 눌림목 백테스트")
    parser.add_argument("--codes", type=str, default="",
                        help="종목코드 (콤마 구분, 미입력 시 시총 상위)")
    parser.add_argument("--days", type=int, default=365, help="백테스트 기간 (일)")
    parser.add_argument("--capital", type=float, default=1_000_000, help="초기 자본금")
    parser.add_argument("--stop-loss", type=float, default=3.0, help="손절 %")
    parser.add_argument("--take-profit", type=float, default=5.0, help="익절 %")
    parser.add_argument("--trailing", type=float, default=2.0, help="트레일링 %")
    parser.add_argument("--max-hold", type=int, default=5, help="최대 보유일")
    parser.add_argument("--top", type=int, default=30, help="시총 상위 N종목")
    args = parser.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        codes = get_top_stocks(args.top)

    result = run_backtest(
        codes=codes,
        days=args.days,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        trailing=args.trailing,
        max_hold=args.max_hold,
        initial_capital=args.capital,
    )
    print_result(result)


if __name__ == "__main__":
    main()

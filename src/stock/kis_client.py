"""한국투자증권 Open API 클라이언트

실전/모의투자 모두 지원.
  - 실전: https://openapi.koreainvestment.com:9443
  - 모의: https://openapivts.koreainvestment.com:29443
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class KISClient:
    """한국투자증권 REST API 래퍼"""

    URL_REAL = "https://openapi.koreainvestment.com:9443"
    URL_VIRTUAL = "https://openapivts.koreainvestment.com:29443"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        account_prod: str = "01",
        is_virtual: bool = True,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.account_prod = account_prod
        self.is_virtual = is_virtual
        self.base_url = self.URL_VIRTUAL if is_virtual else self.URL_REAL

        self._token: str = ""
        self._token_expires: float = 0

        if app_key and app_secret:
            self._refresh_token()
            mode = "모의투자" if is_virtual else "실전"
            logger.info("[KIS] 인증 완료 (%s, 계좌: %s)", mode, account_no)

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    # ── 인증 ──

    def _refresh_token(self):
        try:
            resp = requests.post(
                "%s/oauth2/tokenP" % self.base_url,
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                timeout=10,
            )
            data = resp.json()
            self._token = data.get("access_token", "")
            expires_in = int(data.get("expires_in", 86400))
            self._token_expires = time.time() + expires_in - 600
            logger.info("[KIS] 토큰 발급 완료 (유효: %d초)", expires_in)
        except Exception as e:
            logger.error("[KIS] 토큰 발급 실패: %s", e)

    def _ensure_token(self):
        if time.time() >= self._token_expires:
            self._refresh_token()

    def _headers(self, tr_id: str) -> Dict[str, str]:
        self._ensure_token()
        return {
            "authorization": "Bearer %s" % self._token,
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "Content-Type": "application/json; charset=utf-8",
        }

    # ── 시세 조회 ──

    def get_current_price(self, stock_code: str) -> Optional[Dict]:
        """현재가 조회"""
        try:
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-price" % self.base_url,
                headers=self._headers("FHKST01010100"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                },
                timeout=10,
            )
            data = resp.json()
            output = data.get("output", {})
            if not output:
                return None
            return {
                "price": int(output.get("stck_prpr", 0)),
                "open": int(output.get("stck_oprc", 0)),
                "high": int(output.get("stck_hgpr", 0)),
                "low": int(output.get("stck_lwpr", 0)),
                "volume": int(output.get("acml_vol", 0)),
                "change_pct": float(output.get("prdy_ctrt", 0)),
                "name": output.get("rprs_mrkt_kor_name", ""),
            }
        except Exception as e:
            logger.error("[KIS] 현재가 조회 실패 [%s]: %s", stock_code, e)
            return None

    def get_ohlcv(
        self, stock_code: str, period: str = "D", count: int = 100
    ) -> Optional[pd.DataFrame]:
        """일봉/주봉/월봉 OHLCV 조회

        period: D=일봉, W=주봉, M=월봉
        """
        try:
            import datetime
            end_date = datetime.date.today().strftime("%Y%m%d")
            start_date = (datetime.date.today() - datetime.timedelta(days=count * 2)).strftime("%Y%m%d")

            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice" % self.base_url,
                headers=self._headers("FHKST03010100"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_DATE_1": start_date,
                    "FID_INPUT_DATE_2": end_date,
                    "FID_PERIOD_DIV_CODE": period,
                    "FID_ORG_ADJ_PRC": "0",
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get("output2", [])
            if not items:
                return None

            rows = []
            for item in items:
                if not item.get("stck_bsop_date"):
                    continue
                rows.append({
                    "date": item["stck_bsop_date"],
                    "open": int(item.get("stck_oprc", 0)),
                    "high": int(item.get("stck_hgpr", 0)),
                    "low": int(item.get("stck_lwpr", 0)),
                    "close": int(item.get("stck_clpr", 0)),
                    "volume": int(item.get("acml_vol", 0)),
                })

            if not rows:
                return None

            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return df.tail(count)
        except Exception as e:
            logger.error("[KIS] OHLCV 조회 실패 [%s]: %s", stock_code, e)
            return None

    def get_minute_ohlcv(self, stock_code: str, minute: int = 1) -> Optional[pd.DataFrame]:
        """분봉 OHLCV 조회"""
        try:
            import datetime
            now = datetime.datetime.now().strftime("%H%M%S")

            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice" % self.base_url,
                headers=self._headers("FHKST03010200"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_HOUR_1": now,
                    "FID_PW_DATA_INCU_YN": "Y",
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get("output2", [])
            if not items:
                return None

            rows = []
            for item in items:
                ts = item.get("stck_bsop_date", "") + item.get("stck_cntg_hour", "")
                if not ts or len(ts) < 12:
                    continue
                rows.append({
                    "datetime": ts,
                    "open": int(item.get("stck_oprc", 0)),
                    "high": int(item.get("stck_hgpr", 0)),
                    "low": int(item.get("stck_lwpr", 0)),
                    "close": int(item.get("stck_prpr", 0)),
                    "volume": int(item.get("cntg_vol", 0)),
                })

            if not rows:
                return None

            df = pd.DataFrame(rows)
            df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d%H%M%S")
            df = df.set_index("datetime").sort_index()
            return df
        except Exception as e:
            logger.error("[KIS] 분봉 조회 실패 [%s]: %s", stock_code, e)
            return None

    # ── 잔고 조회 ──

    def get_balance(self) -> Optional[Dict]:
        """계좌 잔고 조회"""
        try:
            tr_id = "VTTC8434R" if self.is_virtual else "TTTC8434R"
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/trading/inquire-balance" % self.base_url,
                headers=self._headers(tr_id),
                params={
                    "CANO": self.account_no,
                    "ACNT_PRDT_CD": self.account_prod,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
                timeout=10,
            )
            data = resp.json()

            holdings = []
            for item in data.get("output1", []):
                qty = int(item.get("hldg_qty", 0))
                if qty > 0:
                    holdings.append({
                        "code": item.get("pdno", ""),
                        "name": item.get("prdt_name", ""),
                        "quantity": qty,
                        "avg_price": int(float(item.get("pchs_avg_pric", 0))),
                        "current_price": int(item.get("prpr", 0)),
                        "pnl_pct": float(item.get("evlu_pfls_rt", 0)),
                    })

            output2 = data.get("output2", [{}])
            summary = output2[0] if output2 else {}

            return {
                "cash": int(summary.get("dnca_tot_amt", 0)),
                "total_eval": int(summary.get("tot_evlu_amt", 0)),
                "holdings": holdings,
            }
        except Exception as e:
            logger.error("[KIS] 잔고 조회 실패: %s", e)
            return None

    # ── 전종목/랭킹 조회 ──

    def get_volume_rank(self, market: str = "J", limit: int = 30) -> List[Dict]:
        """거래대금 상위 종목 조회 (당일 기준)"""
        try:
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/volume-rank" % self.base_url,
                headers=self._headers("FHPST01710000"),
                params={
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_COND_SCR_DIV_CODE": "20101",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "0",
                    "FID_BLNG_CLS_CODE": "0",
                    "FID_TRGT_CLS_CODE": "111111111",
                    "FID_TRGT_EXLS_CLS_CODE": "000000",
                    "FID_INPUT_PRICE_1": "0",
                    "FID_INPUT_PRICE_2": "0",
                    "FID_VOL_CNT": "0",
                    "FID_INPUT_DATE_1": "",
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get("output", [])
            results = []
            for item in items[:limit]:
                code = item.get("mksc_shrn_iscd", "")
                if not code:
                    continue
                results.append({
                    "code": code,
                    "name": item.get("hts_kor_isnm", ""),
                    "price": int(item.get("stck_prpr", 0)),
                    "change_pct": float(item.get("prdy_ctrt", 0)),
                    "volume": int(item.get("acml_vol", 0)),
                    "trade_value": int(item.get("acml_tr_pbmn", 0)),
                })
            return results
        except Exception as e:
            logger.error("[KIS] 거래량 순위 조회 실패: %s", e)
            return []

    def get_price_change_rank(self, direction: str = "up", limit: int = 20) -> List[Dict]:
        """등락률 상위 종목 조회. direction: 'up' 또는 'down'"""
        try:
            # 상승: 순위=0, 하락: 순위=1
            rank_code = "0" if direction == "up" else "1"
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/ranking/fluctuation" % self.base_url,
                headers=self._headers("FHPST01700000"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_SCR_DIV_CODE": "20170",
                    "FID_INPUT_ISCD": "0000",
                    "FID_RANK_SORT_CLS_CODE": rank_code,
                    "FID_INPUT_CNT_1": "0",
                    "FID_PRC_CLS_CODE": "0",
                    "FID_INPUT_PRICE_1": "0",
                    "FID_INPUT_PRICE_2": "0",
                    "FID_VOL_CNT": "0",
                    "FID_TRGT_CLS_CODE": "0",
                    "FID_TRGT_EXLS_CLS_CODE": "0",
                    "FID_DIV_CLS_CODE": "0",
                    "FID_RSFL_RATE1": "",
                    "FID_RSFL_RATE2": "",
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get("output", [])
            results = []
            for item in items[:limit]:
                code = item.get("stck_shrn_iscd", item.get("mksc_shrn_iscd", ""))
                if not code:
                    continue
                results.append({
                    "code": code,
                    "name": item.get("hts_kor_isnm", ""),
                    "price": int(item.get("stck_prpr", 0)),
                    "change_pct": float(item.get("prdy_ctrt", 0)),
                    "volume": int(item.get("acml_vol", 0)),
                    "trade_value": int(item.get("acml_tr_pbmn", 0)),
                })
            return results
        except Exception as e:
            logger.debug("[KIS] 등락률 순위 조회 실패: %s", e)
            return []

    # ── 수급/체결강도/지수 조회 ──

    def get_investor_trend(self, stock_code: str) -> Optional[Dict]:
        """투자자별 매매동향 (외국인/기관 순매수)"""
        try:
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-investor" % self.base_url,
                headers=self._headers("FHKST01010900"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get("output", [])
            if not items:
                return None

            result = {"foreign_net": 0, "institution_net": 0, "program_net": 0}
            for item in items:
                investor = item.get("invst_nm", "")
                buy = int(item.get("seln_qty", 0))
                sell = int(item.get("shnu_qty", 0))
                net = sell - buy

                if "외국인" in investor:
                    result["foreign_net"] = net
                elif "기관" in investor:
                    result["institution_net"] = net
                elif "프로그램" in investor or "투신" in investor:
                    result["program_net"] += net

            return result
        except Exception as e:
            logger.debug("[KIS] 투자자 동향 조회 실패 [%s]: %s", stock_code, e)
            return None

    def get_volume_power(self, stock_code: str) -> float:
        """체결강도 조회 (매수체결량/매도체결량 × 100). 100 이상이면 매수세 우세."""
        try:
            info = self.get_current_price(stock_code)
            if not info:
                return 0.0
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-ccnl" % self.base_url,
                headers=self._headers("FHKST01010300"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                },
                timeout=10,
            )
            data = resp.json()
            items = data.get("output", [])
            buy_vol = 0
            sell_vol = 0
            for item in items[:20]:
                vol = int(item.get("cntg_vol", 0))
                price_change = item.get("prdy_vrss_sign", "3")
                if price_change in ("1", "2"):
                    buy_vol += vol
                elif price_change in ("4", "5"):
                    sell_vol += vol
            if sell_vol > 0:
                return buy_vol / sell_vol * 100
            return 200.0 if buy_vol > 0 else 100.0
        except Exception as e:
            logger.debug("[KIS] 체결강도 조회 실패 [%s]: %s", stock_code, e)
            return 0.0

    def get_index_price(self, index_code: str = "0001") -> Optional[Dict]:
        """코스피/코스닥 지수 조회. 0001=코스피, 1001=코스닥"""
        try:
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-index-price" % self.base_url,
                headers=self._headers("FHPUP02100000"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": index_code,
                },
                timeout=10,
            )
            data = resp.json()
            output = data.get("output", {})
            if not output:
                return None
            return {
                "price": float(output.get("bstp_nmix_prpr", 0)),
                "change_pct": float(output.get("bstp_nmix_prdy_ctrt", 0)),
            }
        except Exception as e:
            logger.debug("[KIS] 지수 조회 실패 [%s]: %s", index_code, e)
            return None

    def get_orderbook_ratio(self, stock_code: str) -> Optional[Dict]:
        """호가창 매수/매도 잔량비 조회"""
        try:
            resp = requests.get(
                "%s/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn" % self.base_url,
                headers=self._headers("FHKST01010200"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                },
                timeout=10,
            )
            data = resp.json()
            output = data.get("output1", {})
            if not output:
                return None

            total_ask = int(output.get("total_askp_rsqn", 0))
            total_bid = int(output.get("total_bidp_rsqn", 0))
            ratio = total_bid / total_ask if total_ask > 0 else 0

            return {
                "total_ask": total_ask,
                "total_bid": total_bid,
                "bid_ask_ratio": ratio,
            }
        except Exception as e:
            logger.debug("[KIS] 호가 잔량 조회 실패 [%s]: %s", stock_code, e)
            return None

    # ── 주문 ──

    def buy(self, stock_code: str, quantity: int, price: int = 0) -> Optional[Dict]:
        """매수 주문. price=0이면 시장가."""
        tr_id = "VTTC0802U" if self.is_virtual else "TTTC0802U"
        ord_type = "01" if price > 0 else "06"
        return self._order(tr_id, stock_code, "buy", ord_type, quantity, price)

    def sell(self, stock_code: str, quantity: int, price: int = 0) -> Optional[Dict]:
        """매도 주문. price=0이면 시장가."""
        tr_id = "VTTC0801U" if self.is_virtual else "TTTC0801U"
        ord_type = "01" if price > 0 else "06"
        return self._order(tr_id, stock_code, "sell", ord_type, quantity, price)

    def _order(
        self, tr_id: str, stock_code: str, side: str,
        ord_type: str, quantity: int, price: int,
    ) -> Optional[Dict]:
        try:
            body = {
                "CANO": self.account_no,
                "ACNT_PRDT_CD": self.account_prod,
                "PDNO": stock_code,
                "ORD_DVSN": ord_type,
                "ORD_QTY": str(quantity),
                "ORD_UNPR": str(price),
            }
            resp = requests.post(
                "%s/uapi/domestic-stock/v1/trading/order-cash" % self.base_url,
                headers=self._headers(tr_id),
                json=body,
                timeout=10,
            )
            data = resp.json()

            rt_cd = data.get("rt_cd", "")
            if rt_cd == "0":
                order_no = data.get("output", {}).get("ODNO", "")
                logger.info("[KIS] %s 주문 완료: %s %d주 (주문번호: %s)",
                            side.upper(), stock_code, quantity, order_no)
                return {"success": True, "order_no": order_no}
            else:
                msg = data.get("msg1", "알 수 없는 오류")
                logger.error("[KIS] %s 주문 실패: %s", side.upper(), msg)
                return {"success": False, "error": msg}
        except Exception as e:
            logger.error("[KIS] %s 주문 오류: %s", side.upper(), e)
            return {"success": False, "error": str(e)}

"""한국투자증권 Open API 클라이언트

KIS Developers: https://apiportal.koreainvestment.com

주요 기능:
  - OAuth 토큰 발급 및 자동 갱신
  - 주식 현재가 / 일봉 조회
  - 매수 / 매도 주문
  - 잔고 조회
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from config.settings import KISConfig

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_BUFFER_SEC = 60


@dataclass
class StockPrice:
    code: str
    name: str
    price: int
    open: int
    high: int
    low: int
    volume: int
    change: int
    change_pct: float
    market_cap: int = 0


@dataclass
class Position:
    code: str
    name: str
    qty: int
    avg_price: float
    current_price: int
    pnl: float
    pnl_pct: float


@dataclass
class OrderResult:
    success: bool
    order_no: str = ""
    message: str = ""
    price: int = 0
    qty: int = 0


class KISClient:
    """한국투자증권 REST API 클라이언트"""

    def __init__(self, config: KISConfig):
        self.config = config
        self._base_url = config.base_url
        self._access_token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json; charset=utf-8"})

    def _ensure_token(self):
        """토큰이 없거나 만료 임박 시 재발급"""
        now = datetime.now()
        if self._access_token and self._token_expires and now < self._token_expires:
            return
        self._issue_token()

    def _issue_token(self):
        url = f"{self._base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
        }
        resp = self._session.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires = datetime.now() + timedelta(seconds=expires_in - TOKEN_EXPIRY_BUFFER_SEC)
        logger.info("KIS 토큰 발급 완료 (만료: %s)", self._token_expires.strftime("%Y-%m-%d %H:%M:%S"))

    def _headers(self, tr_id: str) -> Dict[str, str]:
        self._ensure_token()
        return {
            "authorization": f"Bearer {self._access_token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path: str, tr_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = self._headers(tr_id)
        resp = self._session.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, tr_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = self._headers(tr_id)
        resp = self._session.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ── 시세 조회 ──

    def get_current_price(self, stock_code: str) -> StockPrice:
        """주식 현재가 조회"""
        tr_id = "FHKST01010100"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        data = self._get("/uapi/domestic-stock/v1/quotations/inquire-price", tr_id, params)
        output = data.get("output", {})
        return StockPrice(
            code=stock_code,
            name=output.get("hts_kor_isnm", ""),
            price=int(output.get("stck_prpr", 0)),
            open=int(output.get("stck_oprc", 0)),
            high=int(output.get("stck_hgpr", 0)),
            low=int(output.get("stck_lwpr", 0)),
            volume=int(output.get("acml_vol", 0)),
            change=int(output.get("prdy_vrss", 0)),
            change_pct=float(output.get("prdy_ctrt", 0.0)),
            market_cap=int(output.get("hts_avls", 0)),
        )

    def get_daily_ohlcv(
        self,
        stock_code: str,
        period: str = "D",
        count: int = 100,
        end_date: str = "",
    ) -> pd.DataFrame:
        """일봉/주봉/월봉 OHLCV 조회

        Args:
            stock_code: 종목코드 (예: 005930)
            period: D=일봉, W=주봉, M=월봉
            count: 조회 개수 (최대 100)
            end_date: 종료일 (YYYYMMDD, 빈값=오늘)
        """
        tr_id = "FHKST01010400"
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }
        data = self._get("/uapi/domestic-stock/v1/quotations/inquire-daily-price", tr_id, params)
        output2 = data.get("output2", [])
        if not output2:
            output2 = data.get("output", [])

        rows = []
        for item in output2:
            rows.append({
                "date": item.get("stck_bsop_date", ""),
                "open": int(item.get("stck_oprc", 0)),
                "high": int(item.get("stck_hgpr", 0)),
                "low": int(item.get("stck_lwpr", 0)),
                "close": int(item.get("stck_clpr", 0)),
                "volume": int(item.get("acml_vol", 0)),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
            df = df.sort_values("date").reset_index(drop=True)
        return df

    # ── 주문 ──

    def buy_market(self, stock_code: str, qty: int) -> OrderResult:
        """시장가 매수"""
        return self._place_order(stock_code, qty, order_type="buy", price=0)

    def sell_market(self, stock_code: str, qty: int) -> OrderResult:
        """시장가 매도"""
        return self._place_order(stock_code, qty, order_type="sell", price=0)

    def buy_limit(self, stock_code: str, qty: int, price: int) -> OrderResult:
        """지정가 매수"""
        return self._place_order(stock_code, qty, order_type="buy", price=price)

    def sell_limit(self, stock_code: str, qty: int, price: int) -> OrderResult:
        """지정가 매도"""
        return self._place_order(stock_code, qty, order_type="sell", price=price)

    def _place_order(self, stock_code: str, qty: int, order_type: str, price: int) -> OrderResult:
        path = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.config.is_paper:
            tr_id = "VTTC0802U" if order_type == "buy" else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if order_type == "buy" else "TTTC0801U"

        ord_dvsn = "01" if price > 0 else "01"
        if price == 0:
            ord_dvsn = "01"

        body = {
            "CANO": self.config.account_no[:8],
            "ACNT_PRDT_CD": self.config.account_no[8:] if len(self.config.account_no) > 8 else self.config.account_product_code,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price) if price > 0 else "0",
        }

        try:
            data = self._post(path, tr_id, body)
            rt_cd = data.get("rt_cd", "1")
            msg = data.get("msg1", "")
            output = data.get("output", {})
            order_no = output.get("ODNO", "")

            if rt_cd == "0":
                action = "매수" if order_type == "buy" else "매도"
                logger.info("%s 주문 성공: %s %d주 (주문번호: %s)", action, stock_code, qty, order_no)
                return OrderResult(success=True, order_no=order_no, message=msg, price=price, qty=qty)
            else:
                logger.warning("주문 실패: %s", msg)
                return OrderResult(success=False, message=msg)
        except Exception as e:
            logger.error("주문 에러: %s", e)
            return OrderResult(success=False, message=str(e))

    # ── 잔고 조회 ──

    def get_balance(self) -> List[Position]:
        """주식 잔고 조회"""
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"

        if self.config.is_paper:
            tr_id = "VTTC8434R"
        else:
            tr_id = "TTTC8434R"

        params = {
            "CANO": self.config.account_no[:8],
            "ACNT_PRDT_CD": self.config.account_no[8:] if len(self.config.account_no) > 8 else self.config.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        data = self._get(path, tr_id, params)
        output1 = data.get("output1", [])
        positions = []
        for item in output1:
            qty = int(item.get("hldg_qty", 0))
            if qty <= 0:
                continue
            avg_price = float(item.get("pchs_avg_pric", 0))
            current_price = int(item.get("prpr", 0))
            pnl = float(item.get("evlu_pfls_amt", 0))
            pnl_pct = float(item.get("evlu_pfls_rt", 0))

            positions.append(Position(
                code=item.get("pdno", ""),
                name=item.get("prdt_name", ""),
                qty=qty,
                avg_price=avg_price,
                current_price=current_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
            ))
        return positions

    def get_cash_balance(self) -> int:
        """예수금(주문가능현금) 조회"""
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"

        if self.config.is_paper:
            tr_id = "VTTC8434R"
        else:
            tr_id = "TTTC8434R"

        params = {
            "CANO": self.config.account_no[:8],
            "ACNT_PRDT_CD": self.config.account_no[8:] if len(self.config.account_no) > 8 else self.config.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        data = self._get(path, tr_id, params)
        output2 = data.get("output2", [{}])
        if output2:
            return int(output2[0].get("dnca_tot_amt", 0))
        return 0

    def get_stock_position(self, stock_code: str) -> Optional[Position]:
        """특정 종목 보유 포지션 조회"""
        positions = self.get_balance()
        for pos in positions:
            if pos.code == stock_code:
                return pos
        return None

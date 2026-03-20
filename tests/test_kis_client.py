"""KIS API 클라이언트 테스트"""

from unittest.mock import MagicMock, patch
from datetime import datetime

import pandas as pd
import pytest

from config.settings import KISConfig
from src.api.kis_client import KISClient, StockPrice, Position, OrderResult


@pytest.fixture
def kis_config():
    return KISConfig(
        app_key="test_app_key",
        app_secret="test_app_secret",
        account_no="12345678-01",
        is_paper=True,
    )


@pytest.fixture
def client(kis_config):
    return KISClient(kis_config)


class TestKISConfig:
    def test_paper_url(self):
        config = KISConfig(is_paper=True)
        assert "vts" in config.base_url

    def test_live_url(self):
        config = KISConfig(is_paper=False)
        assert "vts" not in config.base_url

    def test_is_valid(self, kis_config):
        assert kis_config.is_valid

    def test_is_invalid(self):
        config = KISConfig()
        assert not config.is_valid


class TestTokenManagement:
    def test_issue_token(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "test_token_123",
            "expires_in": 86400,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            client._issue_token()

        assert client._access_token == "test_token_123"
        assert client._token_expires is not None

    def test_ensure_token_when_none(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "new_token",
            "expires_in": 86400,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            client._ensure_token()

        assert client._access_token == "new_token"


class TestGetCurrentPrice:
    def test_parse_price(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output": {
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "72000",
                "stck_oprc": "71500",
                "stck_hgpr": "72500",
                "stck_lwpr": "71000",
                "acml_vol": "15000000",
                "prdy_vrss": "500",
                "prdy_ctrt": "0.70",
                "hts_avls": "4300000",
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            price = client.get_current_price("005930")

        assert isinstance(price, StockPrice)
        assert price.code == "005930"
        assert price.name == "삼성전자"
        assert price.price == 72000
        assert price.high == 72500
        assert price.low == 71000
        assert price.volume == 15000000


class TestGetDailyOHLCV:
    def test_parse_ohlcv(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output2": [
                {
                    "stck_bsop_date": "20260320",
                    "stck_oprc": "71500",
                    "stck_hgpr": "72500",
                    "stck_lwpr": "71000",
                    "stck_clpr": "72000",
                    "acml_vol": "15000000",
                },
                {
                    "stck_bsop_date": "20260319",
                    "stck_oprc": "70500",
                    "stck_hgpr": "71500",
                    "stck_lwpr": "70000",
                    "stck_clpr": "71500",
                    "acml_vol": "12000000",
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            df = client.get_daily_ohlcv("005930")

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert df["close"].iloc[-1] == 72000


class TestOrders:
    def test_buy_market_success(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "rt_cd": "0",
            "msg1": "주문완료",
            "output": {"ODNO": "12345"},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.buy_market("005930", 10)

        assert isinstance(result, OrderResult)
        assert result.success
        assert result.order_no == "12345"

    def test_sell_market_success(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "rt_cd": "0",
            "msg1": "주문완료",
            "output": {"ODNO": "67890"},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.sell_market("005930", 5)

        assert result.success

    def test_order_failure(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "rt_cd": "1",
            "msg1": "잔고 부족",
            "output": {},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.buy_market("005930", 10)

        assert not result.success

    def test_order_exception(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        with patch.object(client._session, "post", side_effect=Exception("네트워크 에러")):
            result = client.buy_market("005930", 10)

        assert not result.success
        assert "네트워크 에러" in result.message


class TestBalance:
    def test_get_balance(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "10",
                    "pchs_avg_pric": "70000",
                    "prpr": "72000",
                    "evlu_pfls_amt": "20000",
                    "evlu_pfls_rt": "2.86",
                }
            ],
            "output2": [{"dnca_tot_amt": "5000000"}],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            positions = client.get_balance()

        assert len(positions) == 1
        assert positions[0].code == "005930"
        assert positions[0].qty == 10

    def test_get_cash_balance(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output1": [],
            "output2": [{"dnca_tot_amt": "5000000"}],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            cash = client.get_cash_balance()

        assert cash == 5000000

    def test_get_stock_position_found(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "10",
                    "pchs_avg_pric": "70000",
                    "prpr": "72000",
                    "evlu_pfls_amt": "20000",
                    "evlu_pfls_rt": "2.86",
                },
            ],
            "output2": [{"dnca_tot_amt": "5000000"}],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            pos = client.get_stock_position("005930")

        assert pos is not None
        assert pos.code == "005930"

    def test_get_stock_position_not_found(self, client):
        client._access_token = "test_token"
        client._token_expires = datetime(2099, 1, 1)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "output1": [],
            "output2": [{"dnca_tot_amt": "5000000"}],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            pos = client.get_stock_position("005930")

        assert pos is None

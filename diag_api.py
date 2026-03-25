#!/usr/bin/env python3
"""KIS API 거래량순위 직접 진단 스크립트"""
import os, sys, json, requests

# .env 로드
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

APP_KEY = os.getenv("KIS_APP_KEY", "")
APP_SECRET = os.getenv("KIS_APP_SECRET", "")
BASE = "https://openapi.koreainvestment.com:9443"

if not APP_KEY or not APP_SECRET:
    print("ERROR: KIS_APP_KEY / KIS_APP_SECRET not set")
    sys.exit(1)

# 1. 토큰 발급
print("=== 1. 토큰 발급 ===")
resp = requests.post(f"{BASE}/oauth2/tokenP", json={
    "grant_type": "client_credentials",
    "appkey": APP_KEY,
    "appsecret": APP_SECRET,
}, timeout=10)
data = resp.json()
token = data.get("access_token", "")
if not token:
    print(f"FAIL: {json.dumps(data, ensure_ascii=False, indent=2)}")
    sys.exit(1)
print(f"OK: token={token[:20]}...")

# 2. 거래량순위 조회
print("\n=== 2. 거래량순위 API ===")
headers = {
    "authorization": f"Bearer {token}",
    "appkey": APP_KEY,
    "appsecret": APP_SECRET,
    "tr_id": "FHPST01710000",
    "custtype": "P",
    "Content-Type": "application/json; charset=utf-8",
}
params = {
    "FID_COND_MRKT_DIV_CODE": "J",
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
}
resp = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/volume-rank",
                    headers=headers, params=params, timeout=10)
print(f"HTTP: {resp.status_code}")
data = resp.json()
print(f"rt_cd: {data.get('rt_cd')}")
print(f"msg1: {data.get('msg1', data.get('msg', 'N/A'))}")
output = data.get("output", [])
print(f"output 건수: {len(output)}")

if not output:
    print(f"\n전체 응답:\n{json.dumps(data, ensure_ascii=False, indent=2)[:2000]}")
else:
    print("\n상위 10종목:")
    for i, item in enumerate(output[:10]):
        code = item.get("mksc_shrn_iscd", "?")
        name = item.get("hts_kor_isnm", "?")
        price = item.get("stck_prpr", "?")
        pct = item.get("prdy_ctrt", "?")
        vol = item.get("acml_vol", "?")
        tval = item.get("acml_tr_pbmn", "?")
        print(f"  {i+1:2d}. {code} {name:12s} | {price:>8s}원 | {pct:>6s}% | 거래량:{vol} | 거래대금:{tval}")

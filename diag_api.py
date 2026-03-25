#!/usr/bin/env python3
"""KIS API 거래량순위 직접 진단 스크립트"""
import os, sys, json, time, requests

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

headers = {
    "authorization": f"Bearer {token}",
    "appkey": APP_KEY,
    "appsecret": APP_SECRET,
    "custtype": "P",
    "Content-Type": "application/json; charset=utf-8",
}

# 2. 여러 FID_COND_SCR_DIV_CODE 값 시도
for scr_code in ["20171", "20170", "20101"]:
    print(f"\n=== 거래량순위 (SCR_DIV_CODE={scr_code}) ===")
    headers["tr_id"] = "FHPST01710000"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": scr_code,
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
    data = resp.json()
    output = data.get("output", [])
    print(f"HTTP {resp.status_code} | rt_cd={data.get('rt_cd')} | msg={data.get('msg1','N/A')} | output={len(output)}건")

    if output:
        print("상위 5종목:")
        for i, item in enumerate(output[:5]):
            print(f"  {i+1}. {item.get('mksc_shrn_iscd','?')} {item.get('hts_kor_isnm','?')} "
                  f"| {item.get('stck_prpr','?')}원 | {item.get('prdy_ctrt','?')}%")
        break  # 성공하면 중단

    time.sleep(0.5)  # API 호출 간격

# 3. 대안: 등락률 상위 (FHPST01700000)
print(f"\n=== 등락률순위 (대안 API) ===")
headers["tr_id"] = "FHPST01700000"
params2 = {
    "FID_COND_MRKT_DIV_CODE": "J",
    "FID_COND_SCR_DIV_CODE": "20170",
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
                    headers=headers, params=params2, timeout=10)
data = resp.json()
output = data.get("output", [])
print(f"HTTP {resp.status_code} | rt_cd={data.get('rt_cd')} | msg={data.get('msg1','N/A')} | output={len(output)}건")
if output:
    print("상위 5종목:")
    for i, item in enumerate(output[:5]):
        print(f"  {i+1}. {item.get('mksc_shrn_iscd','?')} {item.get('hts_kor_isnm','?')} "
              f"| {item.get('stck_prpr','?')}원 | {item.get('prdy_ctrt','?')}%")
elif not output:
    print(f"전체 응답: {json.dumps(data, ensure_ascii=False)[:500]}")

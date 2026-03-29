# 거래소 간 재정거래 자동매매 봇 (Crypto Arbitrage Bot)

전 세계 거래소 간의 **USDT/코인 가격 차이**를 실시간으로 분석하여 자동으로 매매 수익을 내도록 설계된 재정거래(아비트라지) 자동매매 솔루션입니다.

## 핵심 기능

- **김치프리미엄 모니터링**: 한국 거래소(업비트, 빗썸) vs 해외 거래소(바이낸스, 바이비트) 간 가격 차이 실시간 추적
- **크로스 거래소 재정거래**: 해외 거래소 간 가격 차이 탐지 및 자동 매매
- **실시간 환율 반영**: KRW/USDT 환율을 자동 조회하여 정확한 가격 비교
- **동시 주문 실행**: 매수/매도를 병렬 실행하여 슬리피지 최소화
- **리스크 관리**: 최소 수익률 필터, 슬리피지 보정, 거래량 검증, 쿨다운
- **실시간 대시보드**: 터미널에서 가격/기회/김프를 한눈에 모니터링

## 지원 거래소

| 거래소 | 유형 | 기준 통화 | 수수료 |
|--------|------|-----------|--------|
| **업비트 (Upbit)** | 한국 | KRW | 0.05% |
| **빗썸 (Bithumb)** | 한국 | KRW | 0.25% |
| **바이낸스 (Binance)** | 해외 | USDT | 0.1% |
| **바이비트 (Bybit)** | 해외 | USDT | 0.1% |

## 프로젝트 구조

```
tradingbot/
├── config/
│   └── settings.py              # 멀티 거래소 설정 관리
├── src/
│   ├── exchanges/
│   │   ├── base_exchange.py     # 거래소 추상 인터페이스
│   │   ├── upbit_exchange.py    # 업비트 전용 클라이언트
│   │   ├── ccxt_exchange.py     # ccxt 기반 범용 클라이언트
│   │   └── exchange_factory.py  # 거래소 팩토리
│   ├── monitor/
│   │   ├── fx_rate.py           # KRW/USDT 환율 조회
│   │   └── price_monitor.py     # 멀티 거래소 동시 가격 조회
│   ├── arbitrage/
│   │   └── detector.py          # 재정거래 기회 탐지 엔진
│   ├── execution/
│   │   └── engine.py            # 동시 주문 실행 엔진
│   ├── risk/
│   │   └── manager.py           # 리스크 관리
│   ├── kr_stock/
│   │   ├── watchlist.py         # 한국 우량주 감시 목록
│   │   ├── data_fetcher.py      # KRX 주식 데이터 수집
│   │   ├── fear_greed_index.py  # 공포/탐욕 지수 엔진
│   │   └── alert_bot.py         # 알림봇 메인 로직
│   ├── utils/
│   │   ├── logger.py            # 로깅
│   │   ├── telegram_bot.py      # 텔레그램 알림
│   │   └── dashboard.py         # 콘솔 대시보드
│   └── main.py                  # 재정거래 봇 엔진
├── tests/                       # 테스트 (105개)
├── run_alert_bot.py             # 공포/탐욕 알림봇 실행
├── run_trader.py                # 기술적 분석 트레이더 실행
├── requirements.txt
├── .env.example
└── README.md
```

## Ubuntu 설치 가이드 (처음부터)

우분투만 설치된 상태에서 시작하는 전체 과정입니다.

### Step 1. 시스템 패키지 업데이트

```bash
sudo apt update && sudo apt upgrade -y
```

### Step 2. Python 3 및 pip 설치

```bash
sudo apt install -y python3 python3-pip python3-venv git
```

설치 확인:

```bash
python3 --version   # Python 3.10 이상 권장
pip3 --version
```

### Step 3. 프로젝트 다운로드

```bash
cd ~
git clone https://github.com/boramee/tradingbot.git
cd tradingbot
```

### Step 4. 가상환경 생성 및 활성화

```bash
python3 -m venv venv
source venv/bin/activate
```

> 이후 터미널을 새로 열 때마다 `source ~/tradingbot/venv/bin/activate` 실행 필요

### Step 5. 파이썬 패키지 설치

```bash
pip install -r requirements.txt
```

### Step 6. 환경 설정 파일 생성

```bash
cp .env.example .env
nano .env
```

`.env` 파일에 사용할 거래소의 API 키를 입력합니다:

```env
# 업비트 (https://upbit.com/mypage/open_api_management 에서 발급)
UPBIT_ACCESS_KEY=발급받은_access_key
UPBIT_SECRET_KEY=발급받은_secret_key

# 바이낸스 (https://www.binance.com/en/my/settings/api-management 에서 발급)
BINANCE_ACCESS_KEY=발급받은_access_key
BINANCE_SECRET_KEY=발급받은_secret_key
```

> `nano` 편집기: 수정 후 `Ctrl+O` → Enter(저장) → `Ctrl+X`(나가기)
>
> API 키 없이도 시세 조회 및 김치프리미엄 모니터링은 가능합니다.

### Step 7. 테스트로 정상 설치 확인

```bash
python3 -m pytest tests/ -v
```

40개 테스트가 모두 `PASSED`로 나오면 정상입니다.

### Step 8. 봇 실행

```bash
# 시뮬레이션 모드로 먼저 테스트 (실제 주문 없음)
python3 -m src.main

# 문제 없으면 실거래 모드
python3 -m src.main --live
```

### (선택) 백그라운드에서 24시간 실행

```bash
# nohup으로 백그라운드 실행
nohup python3 -m src.main --no-dashboard > bot.log 2>&1 &

# 로그 실시간 확인
tail -f bot.log

# 봇 종료
kill $(pgrep -f "src.main")
```

또는 `systemd` 서비스로 등록하면 서버 재시작 시 자동 실행됩니다:

```bash
sudo nano /etc/systemd/system/tradingbot.service
```

아래 내용을 붙여넣기 (`your_username`을 실제 사용자명으로 변경):

```ini
[Unit]
Description=Crypto Arbitrage Trading Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/home/your_username/tradingbot
ExecStart=/home/your_username/tradingbot/venv/bin/python3 -m src.main --no-dashboard
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

서비스 등록 및 실행:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tradingbot    # 부팅 시 자동 시작
sudo systemctl start tradingbot     # 지금 바로 시작
sudo systemctl status tradingbot    # 상태 확인
sudo journalctl -u tradingbot -f    # 로그 실시간 확인
```

---

## 설정 상세

### API 키 설정 (`.env`)

| 거래소 | 키 발급 URL |
|--------|------------|
| 업비트 | https://upbit.com/mypage/open_api_management |
| 빗썸 | https://www.bithumb.com/api_support/management_api |
| 바이낸스 | https://www.binance.com/en/my/settings/api-management |
| 바이비트 | https://www.bybit.com/app/user/api-management |

### 매매 설정 (`.env`)

| 설정 | 설명 | 기본값 |
|------|------|--------|
| `TARGET_SYMBOLS` | 모니터링 코인 | `BTC,ETH,XRP,SOL,DOGE` |
| `MIN_PROFIT_PCT` | 최소 순수익률 (%) | `0.5` |
| `MAX_SLIPPAGE_PCT` | 슬리피지 허용 범위 (%) | `0.3` |
| `MAX_TRADE_USDT` | 1회 최대 거래 금액 (USDT) | `1000` |
| `POLL_INTERVAL_SEC` | 가격 조회 주기 (초) | `2` |
| `KIMCHI_BUY_THRESHOLD` | 김프 매수 기준 (%) | `1.0` |
| `KIMCHI_SELL_THRESHOLD` | 김프 매도 기준 (%) | `3.0` |

## 사용법

### 모니터링 모드 (시뮬레이션, 기본)

```bash
python3 -m src.main
```

### 실거래 모드

```bash
python3 -m src.main --live
```

> 실거래 모드는 5초 대기 후 시작되며, 실제 자금으로 주문이 실행됩니다.

### 대시보드 없이 로그만 출력

```bash
python3 -m src.main --no-dashboard
```

### 테스트 실행

```bash
python3 -m pytest tests/ -v
```

## 작동 원리

### 1. 가격 수집
- 4개 거래소에서 동시에(ThreadPool) 가격 조회
- KRW 가격은 실시간 환율로 USDT로 변환하여 정규화

### 2. 재정거래 기회 탐지
```
스프레드(%) = (매도거래소 매수호가 - 매수거래소 매도호가) / 매수거래소 매도호가 × 100
순수익(%)   = 스프레드(%) - 양쪽 수수료(%)
```

### 3. 리스크 검증
- 순수익률 > 최소 기준(0.5%)인지 확인
- 슬리피지 감안 후에도 수익인지 확인
- 거래량이 충분한지 확인
- 동시 거래 한도, 쿨다운 확인

### 4. 동시 실행
- 매수/매도 거래소에 ThreadPool로 동시 주문
- 한쪽만 체결된 경우 CRITICAL 로그 + 수동 확인 알림

## 김치프리미엄 활용 예시

```
업비트 BTC: 136,350,000 KRW  (환율 1350 → 약 101,000 USDT)
바이낸스 BTC: 100,000 USDT

김치프리미엄 = (101,000 - 100,000) / 100,000 × 100 = +1.0%
```

- 김프 1% 이하: 바이낸스에서 매수 → 업비트로 전송 → 업비트에서 매도 고려
- 김프 3% 이상: 업비트에서 매도 → 바이낸스에서 재매수 고려

---

## 한국 주식시장 공포/탐욕 알림봇

**"시장이 탐욕적일 때 공포에 떨고, 시장이 공포에 떨 때 탐욕을 가져라"** - 워렌 버핏

한국 주식시장 우량주의 공포/탐욕 지수를 실시간으로 분석하여 매수/매도 타이밍을 알려주는 알림봇입니다.

### 공포/탐욕 지수 구성 (0~100)

| 구간 | 점수 | 의미 | 행동 |
|------|------|------|------|
| 극단적 공포 | 0~20 | 시장 패닉 | **강력 매수 기회** |
| 공포 | 20~40 | 약세 심리 | 매수 관심 |
| 중립 | 40~60 | 균형 상태 | 관망 |
| 탐욕 | 60~80 | 과열 조짐 | 매도 관심 |
| 극단적 탐욕 | 80~100 | 버블 위험 | **매도 고려** |

### 세부 지표 (6개)

| 지표 | 비중 | 설명 |
|------|------|------|
| RSI | 25% | 과매수/과매도 판단 (14일) |
| MA 괴리율 | 20% | 200일 이동평균 대비 현재가 |
| 변동성 | 15% | 현재 변동성 vs 1년 평균 |
| 거래량 추세 | 15% | 상승일/하락일 거래량 비율 |
| 52주 위치 | 15% | 52주 최고/최저 내 현재 위치 |
| 볼린저 %B | 10% | 볼린저 밴드 내 위치 |

### 감시 종목

**우량주**: 삼성전자, SK하이닉스, LG에너지솔루션, 삼성SDI, 현대차, 기아, NAVER, 카카오, POSCO홀딩스, LG화학

**ETF**: KODEX 200, KODEX 코스닥150, KODEX 인버스, KODEX 200선물인버스2X

### 사용법

```bash
# 기본 우량주 1회 분석
python3 run_alert_bot.py

# 60분 주기 반복 분석
python3 run_alert_bot.py --loop

# 30분 주기로 반복
python3 run_alert_bot.py --loop --interval 30

# 특정 종목만 분석
python3 run_alert_bot.py --codes 005930,000660,069500

# KOSPI/KOSDAQ 지수 포함 분석
python3 run_alert_bot.py --include-index

# 텔레그램 알림 비활성화 (콘솔만)
python3 run_alert_bot.py --no-telegram
```

### 텔레그램 알림 설정

1. Telegram에서 `@BotFather`에게 `/newbot` 명령으로 봇 생성
2. 발급된 토큰을 `.env` 파일의 `TELEGRAM_TOKEN`에 입력
3. 봇에게 아무 메시지 보낸 후 `https://api.telegram.org/bot<TOKEN>/getUpdates`에서 `chat_id` 확인
4. `.env` 파일의 `TELEGRAM_CHAT_ID`에 입력

극단적 공포/탐욕 구간 진입 시 자동으로 텔레그램 알림이 발송됩니다.

---

## 주의 사항

- **투자 위험**: 재정거래에도 슬리피지, 전송 지연, 가격 변동 리스크가 있습니다.
- **법적 고려**: 한국 거래소와 해외 거래소 간 자금 이동 시 관련 법규를 확인하세요.
- **API 키 보안**: `.env` 파일을 절대 Git에 커밋하지 마세요.
- **소액 테스트**: 시뮬레이션 모드로 충분히 테스트한 후 소액으로 시작하세요.
- **네트워크**: 거래소 API 호출 시 네트워크 지연으로 가격이 변동될 수 있습니다.
- **공포/탐욕 지수**: 기술적 분석 지표를 기반으로 한 참고용 도구이며, 투자 판단의 유일한 근거로 사용하지 마세요.

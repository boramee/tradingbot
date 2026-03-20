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
│   ├── utils/
│   │   ├── logger.py            # 로깅
│   │   └── dashboard.py         # 콘솔 대시보드
│   └── main.py                  # 메인 봇 엔진
├── tests/                       # 테스트 (40개)
├── requirements.txt
├── .env.example
└── README.md
```

## 설치 및 설정

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. API 키 설정

```bash
cp .env.example .env
```

`.env` 파일에 사용할 거래소의 API 키를 입력합니다:

```env
UPBIT_ACCESS_KEY=your_key
UPBIT_SECRET_KEY=your_secret
BINANCE_ACCESS_KEY=your_key
BINANCE_SECRET_KEY=your_secret
```

> API 키 없이도 시세 조회 및 김치프리미엄 모니터링은 가능합니다.

### 3. 매매 설정 (`.env`)

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

## 주의 사항

- **투자 위험**: 재정거래에도 슬리피지, 전송 지연, 가격 변동 리스크가 있습니다.
- **법적 고려**: 한국 거래소와 해외 거래소 간 자금 이동 시 관련 법규를 확인하세요.
- **API 키 보안**: `.env` 파일을 절대 Git에 커밋하지 마세요.
- **소액 테스트**: 시뮬레이션 모드로 충분히 테스트한 후 소액으로 시작하세요.
- **네트워크**: 거래소 API 호출 시 네트워크 지연으로 가격이 변동될 수 있습니다.

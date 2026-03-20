# 업비트 자동매매 봇 (Upbit Trading Bot)

업비트(Upbit) 거래소 API를 활용한 암호화폐 자동매매 프로그램입니다.

## 주요 기능

- **4가지 매매 전략**: RSI, MACD, 볼린저밴드, 복합전략(3개 전략 결합)
- **기술적 분석**: RSI, MACD, 볼린저밴드, 이동평균선, ATR, 거래량 분석
- **리스크 관리**: 손절/익절, 일일 거래 제한, 포지션 비율 제한, 연속 손실 차단
- **백테스트**: 과거 데이터로 전략 성능 검증
- **로깅**: 파일 + 콘솔 동시 로깅

## 프로젝트 구조

```
tradingbot/
├── config/
│   └── settings.py          # 설정 관리 (환경변수 기반)
├── src/
│   ├── exchange/
│   │   └── upbit_client.py  # 업비트 API 클라이언트
│   ├── indicators/
│   │   └── technical.py     # 기술적 분석 지표
│   ├── strategies/
│   │   ├── base_strategy.py     # 전략 인터페이스
│   │   ├── rsi_strategy.py      # RSI 전략
│   │   ├── macd_strategy.py     # MACD 전략
│   │   ├── bollinger_strategy.py # 볼린저밴드 전략
│   │   └── combined_strategy.py  # 복합 전략
│   ├── risk/
│   │   └── manager.py       # 리스크 관리
│   ├── backtest/
│   │   └── engine.py        # 백테스트 엔진
│   ├── utils/
│   │   └── logger.py        # 로깅 설정
│   └── main.py              # 메인 실행 엔진
├── tests/                   # 테스트 코드
├── run_backtest.py          # 백테스트 실행 스크립트
├── requirements.txt         # 의존성
├── .env.example             # 환경변수 예시
└── README.md
```

## 설치 및 설정

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. API 키 설정

[업비트 Open API 관리](https://upbit.com/mypage/open_api_management)에서 API 키를 발급받으세요.

```bash
cp .env.example .env
```

`.env` 파일을 열고 API 키를 입력합니다:

```
UPBIT_ACCESS_KEY=발급받은_access_key
UPBIT_SECRET_KEY=발급받은_secret_key
```

### 3. 매매 설정 (`.env`)

| 설정 | 설명 | 기본값 |
|------|------|--------|
| `TICKER` | 거래 대상 | `KRW-BTC` |
| `INVESTMENT_RATIO` | KRW 잔고 대비 투자 비율 | `0.1` (10%) |
| `MAX_INVESTMENT_KRW` | 1회 최대 투자 금액 | `100000` |
| `STRATEGY` | 전략 선택 | `combined` |
| `STOP_LOSS_PCT` | 손절 기준(%) | `3.0` |
| `TAKE_PROFIT_PCT` | 익절 기준(%) | `5.0` |
| `MAX_DAILY_TRADES` | 일일 최대 거래 횟수 | `10` |

## 사용법

### 자동매매 실행

```bash
# 기본 실행 (60초 간격)
python3 -m src.main

# 실행 간격 지정 (120초)
python3 -m src.main --interval 120

# 1회만 실행
python3 -m src.main --once
```

### 백테스트

```bash
# BTC 일봉 200개로 백테스트
python3 run_backtest.py

# 다른 코인, 시간봉, 데이터 수 지정
python3 run_backtest.py KRW-ETH minute60 500
```

### 테스트 실행

```bash
python3 -m pytest tests/ -v
```

## 매매 전략 설명

### RSI 전략
- **매수**: RSI < 30 (과매도 구간)
- **매도**: RSI > 70 (과매수 구간)
- RSI 추세 반전 시 신뢰도 상향 조정

### MACD 전략
- **매수**: MACD 히스토그램 골든크로스 (음→양)
- **매도**: MACD 히스토그램 데드크로스 (양→음)
- 히스토그램 크기로 신뢰도 판단

### 볼린저밴드 전략
- **매수**: 가격이 하단밴드 이하
- **매도**: 가격이 상단밴드 이상
- %B 지표로 밴드 내 위치 정밀 판단

### 복합 전략 (권장)
- RSI(30%), MACD(35%), 볼린저밴드(35%) 가중 결합
- 이동평균선 추세 확인으로 보정
- 거래량 급등 시 신뢰도 추가 보정

## 리스크 관리

- **손절**: 평균 매수가 대비 N% 하락 시 자동 매도
- **익절**: 평균 매수가 대비 N% 상승 시 자동 매도
- **포지션 제한**: 총 자산 대비 최대 30%까지만 투자
- **일일 거래 제한**: 하루 최대 N회 거래
- **연속 손실 차단**: 3회 연속 손실 시 거래 일시 중단

## 주의 사항

- **투자 손실 위험**: 이 프로그램은 교육/연구 목적으로 제작되었습니다. 실제 투자에 사용할 경우 원금 손실이 발생할 수 있습니다.
- **API 키 보안**: `.env` 파일을 절대 Git에 커밋하지 마세요.
- **소액 테스트**: 처음에는 반드시 소액으로 테스트하세요.
- **백테스트 한계**: 과거 성과가 미래 수익을 보장하지 않습니다.

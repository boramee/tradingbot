# 삼성전자 자동매매 프로그램

한국투자증권(KIS) Open API를 활용한 삼성전자 주식 자동매매 프로그램입니다.

기술적 지표 기반 매매 전략으로 자동 매수/매도를 수행하며, 리스크 관리 및 텔레그램 알림을 지원합니다.

## 주요 기능

- **5가지 매매 전략**: RSI, MACD, 볼린저 밴드, 이동평균 크로스, 복합 전략
- **자동 손절/익절**: 설정한 비율에 도달하면 자동 매도
- **리스크 관리**: 최대 매수금액, 보유수량 제한, 일일 손실 한도
- **모의투자/실전투자**: 한국투자증권 모의투자 환경에서 안전하게 테스트 가능
- **텔레그램 알림**: 매수/매도/손절/익절 시 실시간 알림

## 프로젝트 구조

```
├── run_trader.py          # 메인 실행 파일
├── config/
│   └── settings.py        # 설정 관리
├── src/
│   ├── api/
│   │   └── kis_client.py  # 한국투자증권 API 클라이언트
│   ├── trader/
│   │   └── engine.py      # 자동매매 엔진
│   ├── strategies/        # 매매 전략
│   │   ├── base.py        # 전략 인터페이스
│   │   ├── rsi.py         # RSI 전략
│   │   ├── macd.py        # MACD 전략
│   │   ├── bollinger.py   # 볼린저 밴드 전략
│   │   ├── ma_cross.py    # 이동평균 크로스 전략
│   │   └── combined.py    # 복합 전략
│   ├── indicators/
│   │   └── technical.py   # 기술적 지표 계산
│   ├── risk/
│   │   └── manager.py     # 리스크 관리
│   └── utils/
│       ├── logger.py      # 로깅
│       └── telegram_bot.py # 텔레그램 알림
├── tests/                 # 테스트
├── .env.example           # 환경변수 예시
└── requirements.txt       # 의존성
```

## 설치

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 한국투자증권 API 설정

1. [KIS Developers](https://apiportal.koreainvestment.com)에 가입
2. 앱 키(App Key) 및 앱 시크릿(App Secret) 발급
3. 모의투자 계좌 개설 (실전 전 테스트용)

### 3. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 API 키와 계좌 정보를 입력합니다:

```env
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_시크릿
KIS_ACCOUNT_NO=계좌번호8자리-상품코드2자리
KIS_IS_PAPER=true
```

## 사용법

### 모의투자 (기본)

```bash
python run_trader.py
```

### 실전투자

```bash
python run_trader.py --live
```

> ⚠️ 실전투자는 실제 돈이 사용됩니다. 반드시 모의투자로 충분히 테스트한 후 사용하세요.

### 전략 선택

```bash
# RSI (과매수/과매도 기반)
python run_trader.py --strategy rsi

# MACD (골든크로스/데드크로스 기반)
python run_trader.py --strategy macd

# 볼린저 밴드 (밴드 이탈/복귀 기반)
python run_trader.py --strategy bollinger

# 이동평균 크로스 (5일/20일/60일 이평선)
python run_trader.py --strategy ma_cross

# 복합 전략 (모든 지표 가중 합산, 기본값)
python run_trader.py --strategy combined
```

### 기타 옵션

```bash
# 1회 분석만 실행 (매매 안함)
python run_trader.py --once

# 다른 종목 매매 (삼성전자우)
python run_trader.py --code 005935 --name 삼성전자우

# 매매 주기 30초, 손절 2%, 익절 7%
python run_trader.py --interval 30 --stop-loss 2.0 --take-profit 7.0

# 1회 최대 매수금액 200만원
python run_trader.py --max-amount 2000000
```

## 매매 전략 상세

### RSI (Relative Strength Index)
- RSI 30 이하 → 과매도 → **매수**
- RSI 70 이상 → 과매수 → **매도**

### MACD (Moving Average Convergence Divergence)
- MACD 히스토그램 음→양 전환 (골든크로스) → **매수**
- MACD 히스토그램 양→음 전환 (데드크로스) → **매도**

### 볼린저 밴드
- %B 0.05 이하 (하단 밴드 근접) → **매수**
- %B 0.95 이상 (상단 밴드 근접) → **매도**

### 이동평균 크로스
- 5일선이 20일선 상향 돌파 (골든크로스) → **매수**
- 5일선이 20일선 하향 돌파 (데드크로스) → **매도**
- 60일선으로 장기 추세 확인

### 복합 전략 (기본)
RSI(25%) + MACD(30%) + 볼린저(20%) + 이동평균(25%)을 가중 합산하여 매매 판단.  
거래량이 평균 이하일 경우 신호 강도를 낮춤.

## 리스크 관리

| 항목 | 기본값 | 설정 |
|------|--------|------|
| 1회 최대 매수금액 | 100만원 | `MAX_BUY_AMOUNT` |
| 최대 보유수량 | 100주 | `MAX_HOLD_QTY` |
| 손절 비율 | 3% | `STOP_LOSS_PCT` |
| 익절 비율 | 5% | `TAKE_PROFIT_PCT` |
| 매매 쿨다운 | 5분 | 코드 내 설정 |
| 일일 최대 손실 | 50만원 | 코드 내 설정 |

## 텔레그램 알림 설정

1. Telegram에서 [@BotFather](https://t.me/BotFather)에게 `/newbot` 명령으로 봇 생성
2. 발급받은 토큰을 `.env`의 `TELEGRAM_TOKEN`에 입력
3. 봇에게 아무 메시지를 보낸 후 `https://api.telegram.org/bot<TOKEN>/getUpdates`에서 `chat_id` 확인
4. `.env`의 `TELEGRAM_CHAT_ID`에 입력

## 테스트

```bash
pytest tests/ -v
```

## 주의사항

- 이 프로그램은 투자 참고용이며, 투자 손실에 대한 책임은 사용자에게 있습니다
- 반드시 모의투자로 충분한 테스트를 거친 후 실전투자에 사용하세요
- 한국 주식시장 거래시간(09:00~15:30) 외에는 주문이 체결되지 않습니다
- API 호출 횟수 제한에 주의하세요 (초당 20회 이내 권장)

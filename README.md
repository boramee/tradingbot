# tradingbot

거래소 API에 연결해 **잔고·시세 조회**와 **주문(기본은 드라이런)**까지 이어갈 수 있는 최소 스타터입니다. 전략(시그널·리스크·포지션 관리)은 여기 위에 붙이면 됩니다.

## 전제

- Python 3.10+
- 본인 거래소 계정의 API 키 (**출금 권한은 끄는 것**을 강력 권장)
- 거래소·국가별 규정 및 약관 준수는 사용자 책임입니다.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 설정

```bash
cp .env.example .env
# .env 에 EXCHANGE_ID, API_KEY, API_SECRET 등 입력
```

`EXCHANGE_ID`는 [ccxt 지원 목록](https://github.com/ccxt/ccxt/wiki/Manual#exchanges)의 클래스 이름과 같습니다 (예: `binance`, `upbit`, `bybit`).

일부 거래소는 **국가·IP에서 API 접속이 차단**될 수 있습니다. `451` 등 오류가 나면 다른 거래소 ID로 바꾸거나, 허용된 네트워크에서 실행하세요.

## 사용법

```bash
# 연결·마켓 로드 확인
tradingbot ping

# 잔고
tradingbot balance

# 티커
tradingbot ticker BTC/USDT

# 주문: 기본 DRY_RUN=true 이면 실제 주문 없이 출력만
tradingbot order BTC/USDT buy 0.0001
```

### 실주문 (마지막에만)

1. `.env`에서 `DRY_RUN=false`
2. `TRADING_LIVE_CONFIRM=I_UNDERSTAND` 추가
3. 아주 작은 수량으로 한 번만 검증

실주문 전에 반드시 해당 거래소에서 **최소 주문 단위·심볼 형식**을 확인하세요.

## 다음 단계 (실제 트레이딩 프로그램으로 키우기)

1. **전략 모듈**: 진입/청산 규칙, 포지션 크기, 손절·익절.
2. **실시간 데이터**: REST 폴링 또는 WebSocket(지연·비용에 유리한 쪽 선택).
3. **상태·로그**: 주문 ID, 체결, 에러를 파일/DB에 남겨 재시작 시 복구.
4. **백테스트·페이퍼**: 동일 규칙을 과거 데이터·가상 체결로 먼저 검증.

이 저장소는 1번의 뼈대(API·CLI·안전 기본값)만 제공합니다.

## 보안

- `.env`는 Git에 올리지 마세요.
- 키 유출 시 즉시 거래소에서 폐기·재발급.
- 남이 준 실행 파일·“수익 보장” 봇은 악성코드 가능성을 별도로 의심하세요.

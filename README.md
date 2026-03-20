# tradingbot

거래소 API에 연결해 **잔고·시세·주문(기본 드라이런)**과, **여러 거래소 간 가격 차이(스프레드·김프 스타일)** 스캔을 할 수 있는 스타터입니다.

## 이 프로젝트가 말하는 “프리미엄 / 차익”

- **`spread`**: **같은 심볼**(예: `BTC/USDT`)을 여러 거래소에서 동시에 조회해 **mid(호가 중간) 기준 최저·최고가 차이(%)**를 냅니다. 전 세계 거래소 간 **코인·스테이블 단위 시세 차**를 보는 기본 도구입니다.
- **`kimchi`**: 업비트 `BTC/KRW`·`USDT/KRW`로 **암시적 `BTC/USDT`**를 만들고, 해외 거래소(기본 `binance`)의 `BTC/USDT`와 비교합니다. 말로 하던 **김치 프리미엄(국내 vs 해외) 스타일** 분석에 가깝습니다.
- **`premium`**: (보조) 각 거래소의 **USDT/법정화폐** 호가를 **USD 환율 대비**로 본 **이론가 괴리(%)**입니다. **거래소 A↔B 직접 차익**과는 목적이 다릅니다.

**자동 매매**: `signals` / `simulate-arb`는 **신호·시뮬 출력**만 합니다. `simulate-arb`는 **실주문을 넣지 않습니다.** 실제 차익거래는 **이체·수수료·슬리피지·API 지연** 때문에 주문 로직을 별도로 설계해야 하며, 단일 거래소 주문은 기본 **실주문 차단(`DRY_RUN`)** 입니다.

**수수료 모델**: `ARB_TAKER_FEE_PCT`(기본 0.1%)와 `ARB_FEE_OVERRIDES`로 거래소별 테이커를 넣을 수 있습니다. 순스프레드는 **단순 mid × (1±f)** 가정이며, VIP·메이커·쿠폰은 반영하지 않습니다.

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

# 여러 거래소 동일 심볼 스프레드
tradingbot spread
tradingbot spread --symbols BTC/USDT,ETH/USDT --exchanges binance,okx,kraken
tradingbot spread --watch --interval 5 --min-pct 0.12
tradingbot spread --show-net

# 순스프레드(테이커 수수료 단순 차감) 신호 + 김프 경로 포함 옵션
tradingbot signals --min-net-pct 0.08
tradingbot signals --watch --interval 5 --kimchi --kimchi-base BTC

# 차익 후보만 골라 양다리 주문을 ‘글로’ 시뮬레이션 (실주문 없음)
tradingbot simulate-arb 0.001 --symbols BTC/USDT --exchanges kraken,bitstamp
tradingbot simulate-arb 0.01 --kimchi --kimchi-base BTC

# 업비트 암시 USDT vs 해외 USDT 마켓 (김프 스타일)
tradingbot kimchi --base BTC
tradingbot kimchi --base ETH --global-exchange binance --watch --interval 10

# (보조) USDT/법정화폐 vs USD 환율 괴리
tradingbot premium

# 주문: 기본 DRY_RUN=true 이면 실제 주문 없이 출력만
tradingbot order BTC/USDT buy 0.0001
```

### 실주문 (마지막에만)

1. `.env`에서 `DRY_RUN=false`
2. `TRADING_LIVE_CONFIRM=I_UNDERSTAND` 추가
3. 아주 작은 수량으로 한 번만 검증

실주문 전에 반드시 해당 거래소에서 **최소 주문 단위·심볼 형식**을 확인하세요.

## 다음 단계 (자동 매매로 확장할 때)

1. **실행 레이어**: 스프레드 신호 → 리스크 한도·동시 주문·체결 확인 (특히 **두 거래소 동시**).
2. **실시간**: REST 폴링 대신 WebSocket으로 mid 갱신.
3. **비용 모델**: 출금·입금·네트워크·테이커/메이커 수수료를 빼고도 남는지 계산.
4. **상태·로그**: 주문 ID, 체결, 에러를 파일/DB에 남겨 재시작 시 복구.

## 보안

- `.env`는 Git에 올리지 마세요.
- 키 유출 시 즉시 거래소에서 폐기·재발급.
- 남이 준 실행 파일·“수익 보장” 봇은 악성코드 가능성을 별도로 의심하세요.

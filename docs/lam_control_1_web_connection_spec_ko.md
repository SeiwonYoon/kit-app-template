# LAM Control 1 — 웹 연결(HyView) 설계 (피드백용)

> **상태:** v0.6 — 실무 스트리밍 T2V 흐름 + **Live API 테스트 창(UI)** 요구사항 확정, T2V 이벤트명은 추후  
> **대상:** `morph.lam_control_1` + `sk.hyview_messaging` (신규)  
> **원칙:** TBS와 **통신 구조만** 동일. TBS 이벤트명·응답 스키마 **비재사용**

---

## 0. 확정·미정 요약

| 항목 | 상태 |
|------|------|
| HyView 통신 구조 (T2V → bridge → Kit) | **확정** |
| T2V 이벤트명 | **미정** — 확정 시 handler에 연결하면 **동일 파이프라인 자동 실행** |
| payload 배열 키 | 현재 **`configs` 예정** — contract 상수화 |
| 화면 최대 2개, `{}` = 해당 화면 숨김 | **확정** |
| Kit가 Federation API POST + pagination | **확정** |
| `limit` | **사용자(운영) 설정 가능** — Kit SSOT |
| 전체 fetch 후 CSV 동등 파싱 → prerun → 재생 | **확정** |
| 양쪽 화면 start 시 fetch·시뮬 | **병렬** |
| V2T 응답 형식 | **미정** — 실무 데이터 파악 후 |
| Federation URL·인증 | URL 확정. 인증은 실무 확인 전까지 **선택형 token/header 구조**로 준비 |
| Live API 테스트 **전용 Kit 창** | **확정** — URL·body·인증 입력 → 응답 확인 |

---

## 1. 한 줄 요약

**실무:** `sk.hyview_messaging` + streaming Kit 실행 → 웹 버튼 클릭 → T2V로 `configs`(body) 수신 → Kit가 `limit`·`offset=0`부터 Federation API에 POST → `has_next=false`까지 수집 → 파싱(prerun) → 요청된 화면에서 시뮬.  
이벤트명은 아직 미정이나, **확정되면 handler만 연결**하면 위 흐름이 자동으로 돈다.

---

## 1.1 실무 런타임 흐름 (확정)

```text
[실무 환경]
  Kit — streaming 모드 + sk.hyview_messaging + lam_control_1
  웹 — HyView 페이지 (상단 제어 / 하단 Kit 스트림)

[웹 버튼 클릭]
  → livestream T2V (이벤트명 TBD)
  → payload: configs[] (fab_id, mt, eqp_id, lot_id, mt_from, mt_to …)
              ※ limit, offset 없음

[Kit — sk.hyview_messaging → lam_sim_bridge]
  1) configs 해석 → 화면 표시 (1개 / 2개 50:50 / 한쪽 숨김)
  2) 화면별 body + lam_handler 의 limit + offset=0 으로 POST
  3) pagination.has_next 가 false 될 때까지 offset 증가·반복
  4) rows 병합 → CSV 동등 파싱 → prerun
  5) 해당 화면(들) 시뮬 재생 (2 config 시 병렬)
  6) V2T (형식 TBD)
```

**이벤트명이 확정되면:** `hyview_event_contract.py` 상수 + handler subscribe 만 추가.  
fetch·pagination·파싱·prerun·재생 로직은 **이미 연결된 하나의 파이프라인**을 그대로 탄다.

---

## 2. HyView 통신 구조

```text
웹 T2V
  → sk.hyview_messaging / lam_handler
  → lam_sim_bridge.handle_start_simulation
  → morph.lam_control_1
       ├─ lam_screen_visibility     (화면 표시 — 기존 구현)
       ├─ lam_federation_client     (POST + pagination — 신규)
       ├─ lam_api_timeline_parser   (rows → dwell — 신규)
       └─ build_csv_playback_plan / prerun / 재생 (기존)
  → V2T (미정)
```

---

## 3. T2V — 시뮬 시작 (이벤트명 TBD, 예: `T2V_request_start_simulation`)

### 3.1 Payload (`configs` 예정)

웹 → Kit. 배열 키는 현재 **`configs` 예정**이다. 추후 이름이 바뀔 수 있으므로 handler에 문자열을 흩어 쓰지 않고 contract 상수 한 곳에서 관리한다.

```json
{
  "configs": [
    { "fab_id": "M14", "mt": "202606", "eqp_id": "4EKFA417", "lot_id": "TAJUC44", "mt_from": "202605", "mt_to": "202606" },
    { "fab_id": "M14", "mt": "202606", "eqp_id": "4EKFA417", "lot_id": "TAJUC44", "mt_from": "202605", "mt_to": "202606" }
  ]
}
```

**웹이 보내는 필드 (화면별 body):**

| 필드 | 예시 |
|------|------|
| `fab_id` | `"M14"` |
| `mt` | `"202606"` |
| `eqp_id` | `"4EKFA417"` |
| `lot_id` | `"TAJUC44"` |
| `mt_from` | `"202605"` |
| `mt_to` | `"202606"` |

**웹이 보내지 않음:** `limit`, `offset` (Kit가 추가).

### 3.2 화면 규칙 (확정)

| `configs[0]` | `configs[1]` | 화면 | 동작 |
|--------------|--------------|------|------|
| 데이터 있음 | 데이터 있음 | 1+2 **50:50** | 각 body로 API fetch → **병렬** 파싱·시뮬 |
| `{}` | 데이터 있음 | **2만 표시** (전체 폭) | 화면2 body만 fetch·시뮬 |
| 데이터 있음 | `{}` | **1만 표시** (전체 폭) | 화면1 body만 fetch·시뮬 |
| `{}` | `{}` | — | 오류 (최소 1개 유효 body 필요) |

- `configs[i]` 가 `{}` 이면 해당 화면 **숨김** + fetch·시뮬 **스킵**.
- 구현: `lam_screen_visibility.request_screen_visibility` (이미 있음).
- 숨긴 화면의 stage/runtime은 유지, **표시만** 전환.

### 3.3 양쪽 화면 start — 병렬 (확정)

유효 config 가 2개일 때:

- Federation API fetch: **화면1·2 병렬**
- 파싱·prerun·재생: **화면1·2 독립 세션, 동시 진행**

---

## 4. Kit → Federation API

### 4.1 엔드포인트 (확정)

```http
POST http://federation.digitaltwin.internal/queries/mcc-target-prev-lot-history/run
Content-Type: application/json
```

### 4.2 요청 body

웹 body + Kit가 추가하는 페이지네이션:

```json
{
  "fab_id": "M14",
  "mt": "202606",
  "eqp_id": "4EKFA417",
  "lot_id": "TAJUC44",
  "mt_from": "202605",
  "mt_to": "202606",
  "limit": 1000,
  "offset": 0
}
```

| 필드 | 출처 |
|------|------|
| `fab_id` … `mt_to` | 웹 T2V body (그대로) |
| `limit` | **Kit 설정** (사용자/운영이 변경 가능 — `lam_sim_control_defaults` 등 SSOT) |
| `offset` | Kit — 0부터 시작 |

### 4.3 `limit` 설정 위치 (확정)

- `limit`은 웹 payload에서 받지 않는다.
- LAM용 `sk.hyview_messaging`의 `lam_handler.py`에 **사용자가 쉽게 바꿀 수 있는 설정값**으로 둔다.
- 실제 HTTP·pagination 구현은 별도 client로 분리하더라도, handler의 설정값을 client에 전달한다.
- 기본값(안)은 `1000`이며 어떤 값이든 `has_next=false`까지 반복한다.

### 4.4 Pagination (확정)

```text
offset = 0
all_rows = []
repeat:
  POST (동일 body, 현재 limit·offset)
  all_rows += response.rows
  if pagination.has_next == false: break
  offset += limit    // 예: limit=1000 → 0, 1000, 2000, …
```

- **`has_next` 가 `false` 될 때까지** 반복 — 전체 데이터 수집 필수.
- `limit` 크기(50·100·1000…)는 **사용자 설정**; 구현은 값에 무관하게 동작.
- 타임아웃·재시도 정책은 구현 시 SSOT (넉넉한 전체 fetch 타임아웃).

### 4.5 인증·추가 헤더 (실무 대응 구조)

실무 확인 전에는 **인증 없이도 POST 가능**하고, 필요해지면 코드 한 곳에서 token/header를 추가할 수 있게 한다.

```python
# lam_handler.py 또는 전용 설정 모듈의 예시(구현 시 상세 주석 포함)
FEDERATION_FETCH_LIMIT = 1000

# 인증이 필요 없는 환경: 빈 값 유지
FEDERATION_BEARER_TOKEN = ""
FEDERATION_EXTRA_HEADERS = {}

# 인증이 필요한 환경의 예:
# FEDERATION_BEARER_TOKEN = "실무에서 발급받은 토큰"
# FEDERATION_EXTRA_HEADERS = {
#     "X-API-Key": "실무에서 받은 API 키",
#     "X-Custom-Header": "필요한 값",
# }
```

HTTP 요청 헤더 조립 규칙:

1. 항상 `Content-Type: application/json`
2. token이 비어 있지 않을 때만 `Authorization: Bearer <token>` 추가
3. `FEDERATION_EXTRA_HEADERS`가 있으면 병합
4. token·추가 헤더가 모두 비어 있으면 **인증 헤더 없이 정상 요청**
5. 토큰 값은 로그에 출력하지 않음

> 구현 시 “인증 없음 / Bearer token / API key·커스텀 헤더” 각각을 어디서 설정하는지 한국어 주석으로 명확히 남겨, 실무 환경에서 해당 값만 바꿔 바로 적용할 수 있게 한다.

### 4.6 응답 형식 (확정 — 가정 SSOT)

```json
{
  "query_id": "mcc-target-prev-lot-history",
  "execution_mode": "lake_system_query",
  "row_count": 50,
  "columns": [
    "lot_flag",
    "module_nm",
    "lot_id",
    "cassette_id",
    "cassette_slot",
    "recipe_id",
    "eqp_tm",
    "eqp_start_tm",
    "eqp_end_tm",
    "process_tm"
  ],
  "rows": [
    [
      "prev",
      "AtmArm-EndEffector11",
      "TAGUB84",
      "6PDB5400",
      "25",
      "AJ_SC2HM_R14_M14_2345",
      "2026-06-01 01:04:11.180000",
      "2026-06-01 00:14:27.270000",
      "2026-06-01 00:14:44.420000",
      "0.285833"
    ]
  ],
  "pagination": {
    "limit": 1000,
    "offset": 0,
    "has_next": true
  }
}
```

병합 시: 모든 페이지의 `rows` 를 **순서대로 이어 붙인 뒤** `columns` 기준으로 파싱.

---

## 5. API rows ↔ CSV 파싱 (코드 기준 대조)

현재 CSV 파이프라인 (`simulation_play.py`):

```text
read_csv_rows → normalize_csv_timeline → build_lot_id_to_foup_index
  → rows_to_dwell_records → sort_dwells_for_playback → build_csv_playback_plan …
```

### 5.1 CSV 행에 필요한 필드 (`ParsedCsvRow`)

| 필드 | CSV | API `columns` | 비고 |
|------|-----|---------------|------|
| `module_nm` | 필수 | **있음** | `parse_module_nm_to_slot_key` — **미지원 이름이면 행 스킵** |
| `eqp_start_tm` | 필수 | **있음** | datetime 문자열 → epoch 초 |
| `eqp_end_tm` | 필수 | **있음** | 동일 |
| `process_tm` | 필수 | **있음** | 초(또는 분 보정 로직 있음) |
| `cassette_slot` | 필수 | **있음** | int |
| `lot_id` | 선택(권장) | **있음** | FOUP 매핑(`build_lot_id_to_foup_index`)에 사용 |
| `eqp_id` | 필수(헤더) | **행에 없음** | → **웹 body의 `eqp_id`를 모든 행에 주입** |

### 5.2 API에만 있고 파서가 직접 쓰지 않는 컬럼

| API 컬럼 | 현재 파서 사용 | 메모 |
|----------|----------------|------|
| `lot_flag` | **미사용** | 모든 행을 파서에 전달. 현재 시뮬 동작에는 영향 없음 |
| `cassette_id` | **미사용** | `cassette_slot` 으로 충분 |
| `recipe_id` | **미사용** | UI/로그용으로만 쓸 수 있음 |
| `eqp_tm` | **미사용** | `eqp_start_tm` / `eqp_end_tm` 으로 dwell 구간 계산 |

### 5.3 결론 — 시뮬에 **필수**

API `rows` + 웹 body 조합으로 **CSV와 동등 파싱 가능** (전제):

1. 위 6개 논리 필드 매핑 (`eqp_id`는 body에서)
2. `module_nm` 이 Kit `MODULE_NM_TO_SLOT_KEY` / 정규식에 **매핑 가능**해야 함 — 아니면 해당 행 스킵 (CSV와 동일)
3. `lot_flag`는 필터링하지 않고 **전체 `rows`를 사용**

### 5.4 `lot_flag` 영향 확인 (코드 기준)

현재 CSV 파서가 `ParsedCsvRow`로 읽는 필드는 `eqp_id`, `module_nm`, `lot_id`, `cassette_slot`, `eqp_start_tm`, `eqp_end_tm`, `process_tm`이다. `DwellRecord`와 이송·재생 plan도 이 필드만 사용한다.

따라서:

- `lot_flag` 값은 현재 dwell 생성·이송 경로·재생 시간에 **영향 없음**
- `"prev"`를 포함한 모든 행을 그대로 시뮬 입력에 포함해도 됨
- 단, 같은 dwell 행이 flag별로 **중복 제공**되면 중복 시뮬이 될 수 있으므로 fixture·실응답 검증 시 `(lot_id, cassette_slot, module_nm, eqp_start_tm, eqp_end_tm)` 중복 개수는 로그로 확인
- 향후 `lot_flag` 의미를 동작에 반영할 때만 별도 규칙 추가

### 5.5 구현 경로

```text
API merged rows + columns
  → lam_api_timeline_parser.rows_to_parsed_csv_rows(...)
  → normalize_csv_timeline / build_lot_id_to_foup_index / rows_to_dwell_records
  → (이하 기존 CSV와 동일)
```

로컬: Federation 대신 **동일 JSON fixture** 를 파서에 넣어 검증.

---

## 6. Prerun·재생

TBS `start_simulation` 과 동일 **생명주기**:

1. T2V 수신 → 화면 표시 적용  
2. 화면별 **전체 API fetch** (병렬)  
3. 파싱 → `build_csv_playback_plan` / csv prerun  
4. prerun 완료 후 **자동 재생**  
5. V2T (**형식 미정**)

---

## 7. 설정 SSOT (안)

| 설정 | 설명 | 기본값(안) |
|------|------|------------|
| `FEDERATION_QUERY_URL` | POST URL | 위 내부 URL |
| `FEDERATION_FETCH_LIMIT` | 페이지당 `limit` | 1000 (사용자 변경 가능) |
| `FEDERATION_FETCH_TIMEOUT_SEC` | 화면당 전체 fetch 타임아웃 | TBD |
| `FEDERATION_USE_FIXTURE` | 실 API 대신 샘플 JSON | 로컬 `1` |

---

## 8. V2T — `V2T_response_start_simulation`

**미정** — 실무 데이터·응답 요구사항 파악 후 별도 전달 예정.

---

## 9. 실무 Kit 연결·통합 테스트 (확정)

### 9.1 Live API 테스트 전용 창 (확정 — UI)

웹·스트리밍 연결 **이전·이후** 모두 Federation 연동을 검증하기 위해 Kit 내부에 **별도 `ui.Window` 테스트 창**을 둔다.

| UI 요소 | 설명 |
|---------|------|
| API URL | 기본값: `http://federation.digitaltwin.internal/queries/mcc-target-prev-lot-history/run` (편집 가능) |
| Request body | JSON 멀티라인 (fab_id, mt, eqp_id, lot_id, mt_from, mt_to 샘플 기본 채움) |
| `limit` | 페이지 크기 — `lam_handler` 와 동일 SSOT 또는 창에서 override |
| Bearer token | 비우면 인증 헤더 없음 |
| 추가 헤더 | JSON 또는 key/value (예: `X-API-Key`) — 비우면 생략 |
| **「요청」** | POST 1회 또는 **전 페이지 fetch** (`has_next` 루프) |
| **「파싱·시뮬」** (선택) | fetch 성공 후 동일 파서·prerun·재생까지 (화면 번호 선택) |
| 응답 영역 | HTTP status, pagination, `columns`, `rows` 샘플·요약, 오류 메시지 |

**창 동작 요구:**

- 인증 필드가 **비어 있으면** token/header 없이 POST (주석·툴팁에 명시)
- token·API key 입력 시 `Authorization` / `FEDERATION_EXTRA_HEADERS` 반영
- **토큰 값은 응답 로그·UI에 평문 노출 금지** (마스킹)
- 스트리밍 T2V 경로와 **동일한 HTTP client·pagination·parser** 사용 (코드 중복 금지)
- 배치: `lam_control_1` 또는 `sk.hyview_messaging` — 메뉴/디버그에서 「Federation API 테스트」로 열기

### 9.2 테스트 모드

| 모드 | 목적 | 데이터 |
|------|------|--------|
| Fixture | 개발 PC·오프라인 파서 검증 | 저장된 샘플 JSON |
| Live API | 실무 Kit에서 실제 연결 검증 | Federation 실제 응답 |

`FEDERATION_USE_FIXTURE` 같은 설정 하나로 두 모드를 전환하되, 이후 **응답 검증·병합·파싱·prerun 코드는 완전히 동일한 경로**를 사용한다.

### 9.3 실무 Live API — 창 없이도 동작

테스트 창 외에, 실무는 **웹 T2V → 동일 client** 로만 동작한다. 창은 개발·실무 초기 연결 검증용.

### 9.4 필수 로그

| 구간 | 로그 내용 |
|------|-----------|
| 요청 시작 | URL, 화면 번호, body (**token 제외**), limit, offset |
| 페이지 응답 | HTTP status, query_id, row_count, 실제 `len(rows)`, pagination |
| 응답 데이터 확인 | `columns` 전체 + `rows` 샘플(기본 앞 N개) |
| 전체 fetch 완료 | 페이지 수, 합산 행 수, 소요 시간 |
| 파싱 완료 | 입력 행 수, ParsedCsvRow 수, DwellRecord 수, 스킵·중복 수 |
| prerun 완료 | 화면, schedule/block 수, duration |
| 재생 시작·종료 | 화면별 상태·오류 |

전체 응답을 무조건 콘솔에 모두 찍으면 데이터가 클 때 Kit가 느려질 수 있으므로:

- 기본: metadata + `rows` 앞 N개만 출력
- 상세 진단 설정 ON: 전체 응답 JSON 출력 또는 파일 저장
- 인증 token/header 비밀값은 어떤 모드에서도 마스킹

### 9.5 성공 기준

1. Kit에서 실제 URL로 POST 성공
2. `has_next=false`까지 페이지를 모두 받음
3. 합산 데이터와 샘플 rows가 로그에 보임
4. API 응답이 기존 CSV와 동일한 `DwellRecord` 경로로 파싱됨
5. 해당 화면의 prerun 완료
6. 실제 화면에서 시뮬레이션 재생
7. 두 config이면 두 API 작업·두 시뮬이 병렬로 독립 진행

---

## 10. 기타

- `morph.lam_web_bridge` (HTTP 8720): HyView와 별도 유지.
- streaming layout lock: TBS 패턴 → `lam_hyview_stream` (구현 예정).
- `tbs_control_2` + `lam_control_1` **동시 기동 없음**.

---

## 11. 남은 질문

### Q1. payload 배열 키명

현재 `configs` 예정. 변경 시 contract 상수 한 곳만 수정.

### Q2. `limit` 기본값·설정 방식

`lam_handler.py`에서 사용자가 변경 가능하도록 하는 것은 확정. 기본값을 `1000`으로 확정할지만 남음.

### Q3. Live API 테스트 창

위 §9.1 UI로 확정. 메뉴 진입 위치만 추후 정하면 됨.

### Q4. Federation 인증

실무에서 토큰·헤더 필요 여부 — 확정 시 알려주세요.

> `recipe_id` / `eqp_tm` 활용은 현재 범위에서 제외.

---

## 12. Federation URL·인증 쉬운 설명

이전 문서 Q5는 다음을 묻는 것이었습니다.

**Kit가 서버에 데이터를 요청할 때:**

1. **주소(URL)** — `http://federation.digitaltwin.internal/...` 를 코드에 박을지, 설정 파일·환경변수로 바꿀 수 있게 할지  
2. **인증** — 내부망이라도 API 키·Bearer 토큰·쿠키 등이 필요한지, 필요하면 Kit가 어디서 그 값을 읽을지  

즉 “**누구나 POST하면 되는지, 로그인 정보가 필요한지, URL을 배포마다 바꿀 수 있는지**”를 구현 전에 정하는 질문입니다.  
지금은 URL만 확정, 인증은 **실무 확인 후** 반영하면 됩니다.

---

## 13. 구현 단계

| 단계 | 내용 |
|------|------|
| 1 | `sk.hyview_messaging` + `handle_start_simulation` 골격 |
| 2 | `lam_federation_client` (pagination, fixture/live 전환, 사용자 `limit`, 선택형 인증 헤더) |
| 3 | `lam_api_timeline_parser` → 기존 dwell 파이프라인 연결 |
| 4 | 화면 visibility + **병렬** fetch/prerun/재생 |
| 5 | **Live API 테스트 `ui.Window`** (URL·body·인증·응답) + 스트리밍 T2V와 동일 client |
| 6 | V2T (실무 확정 후) |

---

## 14. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-07-15 | v0.1~v0.3 |
| 2026-07-15 | v0.4 — 피드백 반영: bodys/configs 미정, 화면 규칙·병렬·limit 사용자 설정, API↔CSV 필드 대조, Q5 쉬운 설명, V2T 유보 |
| 2026-07-15 | v0.5 — `configs`, `lot_flag`, `lam_handler` limit, 선택형 인증, 통합 테스트 |
| 2026-07-15 | v0.6 — 실무 streaming T2V 흐름, 이벤트명 TBD·파이프라인 자동 연결, **Live API 테스트 전용 ui.Window** |

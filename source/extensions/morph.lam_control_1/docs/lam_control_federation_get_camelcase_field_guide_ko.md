# LAM Federation Simulation GET — camelCase 필드 대응 가이드

Simulation GET API 응답이 snake_case(`cassette_slot`) 대신 camelCase(`cassetteSlot`)로 올 때 수정 위치와 절차를 정리한다.

현재 구현은 **snake_case를 기본**으로 하며, `lam_api_timeline_parser.py`의 `_API_ROW_FIELD_ALIASES`에 일부 camelCase alias가 이미 등록되어 있다.

---

## 1. 데이터 흐름

```text
GET [{...}, {...}]
  → lam_federation_client.fetch_simulation_get_*
  → lam_api_timeline_parser.object_array_to_merged()
       각 객체: normalize_api_row_dict()
  → rows_to_parsed_csv_rows() / merged_response_to_dwells()
  → simulation_play (dwell / 재생)
```

**필드명 정규화의 단일 진입점:** `normalize_api_row_dict()`  
→ camelCase 대응은 **우선 이 함수와 `_API_ROW_FIELD_ALIASES`만 수정**하면 된다.

---

## 2. 수정 파일 요약

| 우선순위 | 파일 | 수정 내용 |
|----------|------|-----------|
| 1 | `lam_api_timeline_parser.py` | `_API_ROW_FIELD_ALIASES`에 camelCase 키 추가 |
| 2 | `lam_api_timeline_parser.py` | `normalize_api_row_dict()` — alias 매핑 규칙 조정 |
| 3 | (필요 시) `lam_api_timeline_parser.py` | `rows_to_parsed_csv_rows()` — 새 필수 필드 검증 |
| 4 | (필요 시) `simulation_play.py` | dwell/이송에 쓰는 필드가 새 이름이면 `ParsedCsvRow` 경로 확인 |

HTTP GET URL·pagination·화면 분기는 **필드명과 무관**하므로 `lam_federation_client.py`, `lam_federation_pipeline.py`는 보통 수정하지 않는다.

---

## 3. alias 추가 예시

`lam_api_timeline_parser.py`:

```python
_API_ROW_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "module_nm": ("module_nm", "moduleNm"),
    "lot_id": ("lot_id", "lotId"),
    "eqp_id": ("eqp_id", "eqpId"),
    "cassette_slot": ("cassette_slot", "cassetteSlot"),
    "eqp_start_tm": ("eqp_start_tm", "eqpStartTm"),
    "eqp_end_tm": ("eqp_end_tm", "eqpEndTm"),
    "process_tm": ("process_tm", "processTm"),
    # 새 필드 예:
    # "recipe_id": ("recipe_id", "recipeId"),
}
```

규칙:

- **canonical 이름은 항상 snake_case** (기존 CSV·POST Federation과 동일).
- tuple **첫 값이 canonical**, 이후 값이 API에서 올 수 있는 대체 키.
- canonical에 값이 있으면 alias는 덮어쓰지 않는다.

---

## 4. 시뮬에 실제로 쓰는 필드

| canonical | 용도 | camelCase 예 |
|-----------|------|----------------|
| `module_nm` | 모듈 → slot_key 매핑 | `moduleNm` |
| `eqp_start_tm` | dwell 시작 시각 | `eqpStartTm` |
| `eqp_end_tm` | dwell 종료 시각 | `eqpEndTm` |
| `process_tm` | 공정 시간(보조) | `processTm` |
| `cassette_slot` | FOUP 슬롯·웨이퍼 투어 키 | `cassetteSlot` |
| `lot_id` | FOUP 매핑 | `lotId` |
| `eqp_id` | GET 응답 row별 설비 ID | `eqpId` |

**사용하지 않음:** `cassette_id`, `simul_execId` (파싱·재생 경로에서 무시)

---

## 5. 검증 절차

1. **테스트 창** — GET URL로 「GET 1회」→ 응답 로그에서 키 이름 확인.
2. **파싱 통계** — 콘솔 `[LAM/api-parser] parse: rows=… parsed=… skip=…` 확인.
3. **skip 급증** — alias 누락 또는 `module_nm` 미매핑 가능성.
4. **GET 파싱/시뮬** — dwell 수·재생 시작 확인.

---

## 6. object_array_to_merged 동작

`object_array_to_merged()`는 각 객체에 `normalize_api_row_dict()`를 적용한 뒤 `columns`/`rows` matrix로 변환한다.

따라서 alias만 추가하면 POST Federation(`columns`/`rows`) 응답을 수동 붙여넣는 경우에도 동일하게 적용된다.

---

## 7. 주의사항

- **혼합 키**(같은 row에 `cassette_slot`과 `cassetteSlot` 동시 존재): canonical 값이 우선.
- **중첩 JSON**(필드가 객체/배열): 현재 파서는 flat dict만 지원. 구조 변경 시 `normalize_api_row_dict` 전처리 필요.
- **대소문자만 다른 키**(`EQP_ID` 등): alias tuple에 추가하거나 정규화 단계에서 lower/snake 변환 로직 추가.

---

## 8. 관련 설정

| 설정 | 설명 |
|------|------|
| `FEDERATION_SIMULATION_GET_BASE_URL` | 실무 GET base (`lam_sim_control_defaults.py`) |
| `FEDERATION_FETCH_LIMIT` | GET `limit` query 기본값 |
| 테스트 창 `_DEFAULT_GET_EXEC_ID` | 테스트 URL 기본 execId (`lam_federation_test_window.py`) |

---

## 9. 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-07-27 | Simulation GET(execId) 도입, camelCase 가이드 최초 작성 |

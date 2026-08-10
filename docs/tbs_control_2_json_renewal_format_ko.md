# JSON `renewal` 마커 형식 (TBS Control 2)

## 목적

한 JSON 안에 OHT 하차·FOUP 안착·OHT 퇴장이 모두 들어 있을 때, **FOUP가 포트에 실제로 안착하는 순간**에 포트 패널·막대 5상태를 맞추기 위한 **시간 마커**입니다.

- **재생 길이·배속·sim 공정 구간에는 영향 없음** (0초 스텝)
- **공정이 끝나야 다음 이벤트**로 넘어가는 틀은 그대로

## 파일 형식

시퀀스 루트는 **JSON 배열**입니다. 그 안에 일반 스텝과 같이 객체 하나를 끼워 넣습니다.

```json
[
  { "type": "TIMESAMPLES_REPLAY", "...": "..." },
  { "type": "MOVE", "prim": "/World/...", "duration": 5.0, "...": "..." },
  {
    "renewal": true,
    "description": "renewal: FOUP 안착 — port/bar sync"
  },
  { "type": "MOVE", "prim": "/World/...", "duration": 6.0, "...": "..." }
]
```

### 필수·선택 필드

| 필드 | 필수 | 설명 |
|------|------|------|
| `renewal` | **예** | `true` — 마커임을 표시 |
| `type` | 아니오 | 없어도 됨. 로드 시 내부적으로 `RENEWAL` 로 정규화 |
| `description` | 아니오 | 편집·로그용 메모 |

`duration`, `prim`, `ref` 등 **넣지 않습니다.**

## 런타임 정책 (합의)

| 상황 | 포트·막대 갱신 |
|------|----------------|
| JSON에 `renewal` **없음** | JSON **종료 sim 시각** plan milestone |
| JSON에 `renewal` **있음** | 프리런 plan @ `t_playback_port_sync` — 재생 중 **`sim_now` lookup** (LAM wall 은 3D만) |
| renewal **여러 개** (예외) | **첫 번째만** 사용 (B안) |
| 빈 배열 `[]` | 해당 없음 |

위치 초기화(포즈 리셋)는 **JSON 시작 시** (기존과 동일).

## 엔진·추정 시간

- `sequence_renewal.py` — 마커 판별
- `tbs_lam_sequence_engine` — RENEWAL 스텝은 **duration 0**, 애니 없음
- `_estimate_sequence_total_duration_sec_for_log` — RENEWAL은 **0초**로 합산

## 재생 SSOT (프리런 재생)

1. 프리런: `playback_schedule` + `PlaybackPlanSnapshot` — renewal occ @ sim 시각 확정
2. 재생: `refresh_playback_display_at_sim` → `plan.lookup(sim_now)` — 포트·막대 단일 경로
3. LAM renewal wall: 3D 애니만 — UI 갱신·스케줄 재조회 없음

구현: `control_sim_playback_plan.py`, `playback_plan.py`, `playback_schedule.py`, `json_playback_timing.py`

관련: `docs/tbs_control_2_playback_schedule_rules_ko.md`

# `lam/` — LAM 데이터 루트

본 폴더는 신규 확장 `morph.lam_control` 이 사용하는 **모든 외부 데이터 자산** 의 단일 진실 원천(SoT) 입니다. 코드(`source/extensions/morph.lam_control/`) 와 분리되어 있어 자산 갱신만으로 시뮬/애니 작업이 가능합니다.

위치 약속:
- 본 폴더는 **repo 루트(`source`, `resource`, `templates` 와 같은 레벨)** 에 있어야 합니다.
- LAM 코드(`lam_window.py`)는 `__file__` 에서 부모를 거슬러 올라가며 `lam/` 폴더가 있는 첫 위치를 자동으로 찾습니다(`_find_lam_data_root`). 따라서 source 빌드/_build 어느 쪽이든 같은 위치를 봅니다.

## 하위 폴더

| 폴더 | 용도 |
|---|---|
| `usd/` | LAM 으로 로드할 USD 자산. `master.usd` (LAM Master Save 결과) 와 reference 대상 USD 들을 둡니다. 절대 경로 등록도 가능하지만 **상대 경로(REQ-005 P-2)** 권장. |
| `lam_event_sequences/` | 외부 결과의 `event` 이름과 1:1 매칭되는 시퀀스 파일들. `event_1.json ~ event_5.json` 5개 placeholder 가 있고 LAM 시퀀스 편집기에서 채울 수 있습니다. |
| `lam_external_results/` | 외부 시뮬레이션의 결과 JSON 라인 파일. `sample_external_result.json` 형식 — `[{"t": 0.0, "event": "event_1"}, ...]` 시간 오름차순 배열. LAM Window 의 `Run External` 입력으로 사용. |

## 코드 동작 약속

1. LAM Window startup 시 `_find_lam_data_root()` 가 본 폴더를 탐지하여 모든 기본 경로 textbox 가 본 폴더를 가리키도록 채웁니다.
2. 외부 결과 러너(`lam_external_event_runner`)는 `event=event_N` → `lam/lam_event_sequences/event_N.json` 으로 매칭합니다(찾지 못하면 `seq=NOT_FOUND` 로그).
3. `Save Master…` 가 본 폴더의 `usd/master.usd` 에 저장(또는 사용자 지정 경로). `Open Master…` 후 L2 Discovery 가 그 master 안의 인스턴스를 자동 복원.

자세한 사양은 `source/extensions/morph.lam_control/docs/LAM_Spec.md` 와 `source/extensions/morph.tbs_control_1/docs/USD_Timeline_Spec.md` 의 REQ-002 ~ REQ-006 카드를 참조.

**Jenkins / Linux 배포** (csv · USD · `lam_event_sequences` 가 서버에 안 올라가는 문제):
`source/extensions/morph.lam_control/docs/LAM_Jenkins_Deployment_Guide.md`

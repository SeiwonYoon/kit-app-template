"""morph.lam_control_1 — LAM Multi-USD 독립 타임라인 재생 확장 (듀얼 화면·CSV 프리런 개발).

본 패키지는 `morph.tbs_control_1` 와 **물리적으로 코드 격리** 된다.
- 어떤 모듈도 `morph.tbs_control_1.*` 를 import 하지 않는다.
- `omni.timeline.set_current_time()` 을 호출하지 않는다.
  (USD_Timeline_Spec.md §3.1 단일 stage 멀티 평가 한계 회피, REQ-004)

레이아웃은 USD_Timeline_Spec.md REQ-004 의 5-Layer 와 1:1 매핑이다.
- L1 Asset Loading       → lam_master_stage.py + lam_multi_usd_loader.py
- L2 Composition Discovery → lam_composition_discovery.py
- L3 Instance Registry    → lam_instance_registry.py
- L4 Playback Scheduler   → lam_playback_scheduler.py
- L5 Runtime Evaluator    → lam_runtime_evaluator.py

지원 모듈
- lam_id_resolver.py      → REQ-006 4-튜플 ref + 우선순위 Resolver
- lam_sequence_engine.py  → LAM 시퀀스 step 실행기 (USD_TIMELINE/MOVE/ROTATE/DELAY)
- lam_sequence_editor.py  → LAM 시퀀스 편집기 UI
- lam_external_event_runner.py → 외부 시뮬 결과 JSON 라인 → 시퀀스 트리거
- lam_window.py           → 메인 창(다중 USD 로드 + 시퀀스 편집기 진입)
"""

from .extension import LamControlExtension  # noqa: F401

__all__ = ["LamControlExtension"]

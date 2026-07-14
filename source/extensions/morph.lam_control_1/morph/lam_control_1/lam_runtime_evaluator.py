"""L5 — Runtime Evaluator.

매 프레임 heartbeat:
- `omni.kit.app.get_app().get_update_event_stream()` 의 update event 1 회당
- L3 Registry 의 모든 `state == "playing"` 인스턴스의 `virtual_time` 을
  `dt * speed` 만큼 진행시키고
- 그 시각에서의 attribute 값을 master stage 의 root layer 에 default value 로 reauthor
  (`lam_attribute_reauthor.AttributeReauthorCache`).

본 모듈은 절대 `omni.timeline.set_current_time()` 을 호출하지 않는다.
이는 USD_Timeline_Spec.md §3.1 (단일 stage 멀티 평가 불가) 한계를 우회하기 위함.
인스턴스마다 자기 가상 시각을 갖고 있으므로 같은 master stage 안에서도
서로 다른 시각의 attribute 값을 동시에 reauthor 할 수 있다.

본 모듈은 master stage 가 set 되지 않은 환경에서도 import / start / stop 이 깨지지
않도록 구현되어 있다(reauthor 만 no-op).
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .lam_attribute_reauthor import AttributeReauthorCache
from .lam_instance_registry import AnimationInstanceRegistry
from .lam_instance_runtime import AnimationInstanceRuntime
from .lam_types import AnimationInstance, LAM_FIXED_FPS

# omni.timeline 은 main thread 에서만 사용되며, evaluator 의 _on_update 도 main thread 에서
# 호출되므로 top-level import 가 안전하다. 미설치 환경에서도 import 자체는 성공한다.
try:
    import omni.timeline as _ot  # type: ignore
except Exception:  # pragma: no cover
    _ot = None  # type: ignore

# pxr.Sdf 도 main thread 에서 evaluator 가 사용. _on_update 가 main thread 라 안전.
try:
    from pxr import Sdf as _Sdf  # type: ignore
except Exception:  # pragma: no cover
    _Sdf = None  # type: ignore

# pxr.Usd 는 evaluate_and_write 호출 직전에 EditTarget 을 인스턴스 sublayer 로 잠깐
# 옮길 때만 사용한다 (main thread). 미설치 환경에서도 import 자체는 성공.
try:
    from pxr import Usd as _Usd  # type: ignore
except Exception:  # pragma: no cover
    _Usd = None  # type: ignore


_PRINT_PREFIX = "[LAM/L5]"


class RuntimeEvaluator:
    """LAM 전용 가상 시각 진행기 + reauthor 디스패처."""

    def __init__(self, registry: AnimationInstanceRegistry) -> None:
        self._registry = registry
        self._update_sub = None  # carb event subscription
        self._last_perf: float = 0.0
        self._verbose: bool = False  # True 로 두면 매 프레임 한 줄씩 진행 로그(디버그용).
        # Phase 2: master stage hook + reauthor 캐시.
        self._master = None  # MasterStage (lam_window 가 set_master 로 주입)
        self._reauthor = AttributeReauthorCache()
        # Phase 4: global speed scale (External Runner 가 조정 가능).
        self._global_speed: float = 1.0
        # Phase 6 Hotfix4: omni.timeline 기반 stage 평가 모드.
        # 사용자 USD 가 attribute reauthor (default value) 로 마스킹되지 않는 reference timeSamples
        # 를 가질 때(가설 B) 화면 변화가 없어, evaluator 가 매 프레임 omni.timeline.set_current_time
        # 으로 stage 평가를 트리거한다.
        # - 단일 인스턴스 재생: 정상 동작 (사용자 슬라이더 조작과 동일).
        # - 다중 인스턴스 동시 재생: 마지막 set 인스턴스의 vt 가 winner (단일 stage 한계).
        self._use_omni_timeline: bool = True
        self._timeline_iface = None  # 처음 호출 시 lazily 캐싱.
        self._timeline_warned_once: bool = False
        self._stage_end_warned_once: bool = False
        # Phase 6 Hotfix5 — 비활성 인스턴스 freeze (SdfLayerOffset scale=0).
        # 단일 stage 멀티 평가 한계 우회: 활성 USD_TIMELINE step 의 인스턴스 외에는
        # reference 의 layerOffset.scale=0 으로 시각 평가를 freeze 시켜 같이 재생되지 않도록 한다.
        # state 전환 감지 시점에만 author (매 frame 아님 — 비용 ↓).
        self._last_state_seen: dict[str, str] = {}
        self._freeze_warned_once: bool = False
        # Phase 6 Hotfix6 — Per-Instance Layer Offset Mapping (진정한 independent playback).
        # master_seconds 는 evaluator 의 자체 wall clock(인스턴스 상태와 무관, dt 누적).
        # 인스턴스의 reference 에 author 되는 SdfLayerOffset(offset, scale) 는
        # `inst_tc = offset + master_tc * scale` 매핑을 만든다.
        #   playing 진입 시 한 번만 author:
        #     scale  = inst.speed
        #     offset = (inst.virtual_time*tps + asset_start_tc) - start_master_tc * inst.speed
        #   stopped/paused 진입 시:
        #     scale  = 0, offset = 그 시점의 inst_tc(= freeze frame)
        # 매 frame stage current time = master_seconds 를 set → 모든 reference 가 자기 매핑으로
        # 자기 inst_tc 로 평가 → 진정한 independent playback (다중 활성 인스턴스도 각자 다른 vt).
        self._master_seconds: float = 0.0
        # state 외에도 mapping 을 좌우하는 (state, vt_at_resync, speed, loop_lap) 시그니처.
        # 이 시그니처가 바뀌면 layerOffset 을 다시 author. loop wrap / speed 변경 / 중간 seek 등
        # 모두 시그니처 변경으로 캡처된다.
        self._last_mapping_sig: dict[str, tuple] = {}
        # 한 번이라도 playing 인스턴스가 있을 때만 stage current time 을 set 한다.
        # (모든 인스턴스가 stopped 면 사용자가 슬라이더로 직접 조작 가능하도록 stage 보존.)
        self._last_was_any_playing: bool = False

        # ------------------------------------------------------------------
        # Phase B-1 + B-2-a (2026-05-11) — Option E (offscreen Stage + master mirror)
        # `docs/LAM_Independent_Playback_Plan.md` §5 Phase B / docs/daily/2026-05-11.md.
        #
        # True (현재 기본값) — 새 경로(`_on_update_option_e`) 사용. instance 마다
        #                      `AnimationInstanceRuntime` 1 개를 두고 offscreen Stage 에서
        #                      자기 virtual_time 으로 평가한 attribute 값을 master stage 의
        #                      mirror prim 의 default 로 write. master stage 의 timeline 은
        #                      진행 안 함 (`omni.timeline.set_current_time` 미호출).
        #                      master 의 자산 reference 는 LAM session sublayer 안에서
        #                      `LayerOffset(0, 1e-9)` 로 freeze (`_ensure_option_e_freeze`).
        # False              — 핫픽스 6-10 의 옛 경로 (rollback 용). LayerOffset 매핑
        #                      / sublayer mapping flow / stage current time set 진행.
        #
        # 결정 (2026-05-11) — 사용자 합의로 기본값을 True 로 전환. 검증/회귀가 필요하면
        # 코드 직접 변경 또는 `set_use_option_e(False)` 콘솔 호출로 즉시 rollback 가능.
        # Phase D 안정화 시 flag 자체를 제거 + False 경로 (핫픽스 6-10) 의 dead code
        # 일괄 삭제.
        # ------------------------------------------------------------------
        self._RUNTIME_USE_OPTION_E: bool = True
        # Option E 경로 전용 — instance prim_path → 그 instance 의 runtime 객체.
        # flag=False 인 동안에는 항상 비어있다 (lazy 생성).
        self._runtime_by_path: Dict[str, AnimationInstanceRuntime] = {}
        # Option E 경로에서 1 회만 보고하는 진단 flag.
        self._diag_option_e_announced: bool = False
        # Phase B-2-a — master 의 자산 reference 를 LAM session sublayer 안에서
        # LayerOffset(0, 1e-9) 로 freeze 한 instance prim_path 들의 집합.
        # 1 회만 author. unregister 시 forget_instance 가 청소.
        self._option_e_freeze_seen: set = set()
        # USD_TIMELINE (TBS 스타일) — `omni.timeline` 으로 master stage 시간을 진행하는 동안
        # 해당 인스턴스는 Option E freeze + offscreen evaluate 를 건너뛴다 (reference 가 전역
        # 타임에 따라 평가되어야 함).
        self._master_timeline_prims: Set[str] = set()
        # TIMESAMPLES_REPLAY 핵심 — master 의 인스턴스 산하 OmniGraph 류 prim 을
        # instance sublayer 에서 ``over { active = false }`` 로 비활성화한 prim_path 들.
        # LayerOffset(0, 1e-9) freeze 는 reference 안의 timeSamples 만 정지시키지만
        # OmniGraph 는 자기 tick 으로 평가되어 매 frame ``xformOp:*`` 를 push 하기 때문에,
        # 별도로 prim 자체를 deactivate 하지 않으면 LAM 의 reauthor 가 마스킹된다.
        # `_option_e_omnigraph_paths` 에는 인스턴스별 OmniGraph prim_path 목록을 캐시.
        self._option_e_omnigraph_deactivated_seen: Set[str] = set()
        self._option_e_omnigraph_paths: Dict[str, list] = {}
        # Bake 진행 중인 prim 표식 — 이 prim 에는 매 update tick 의
        # ``_ensure_option_e_freeze`` / ``_ensure_option_e_omnigraph_deactivated``
        # 가 어떤 author 도 하지 않는다. 그렇지 않으면 bake 가 ``await`` 으로 다음
        # update tick 으로 넘어가는 사이 evaluator 가 OmniGraph 를 다시 deactivate
        # 하거나 LayerOffset freeze 를 다시 author 하여 master timeline scrub 이
        # 평가되지 못한다(사용자 회귀 로그 재현 — 2026-05-12).
        self._bake_in_progress_prims: Set[str] = set()
        # 2026-05-13 사용자 요청: "타임라인을 막는 코드 자체를 모두 제거" 정책에 맞춰
        # evaluator 의 default 쓰기를 명시적 gate 로 제한한다. 본 set 에 들어간 prim 만
        # `_on_update_option_e` 가 `evaluate_and_write` 를 호출 → instance sublayer 의
        # default 가 reference timeSamples 위에 winner 로 박힌다. set 에 없으면
        # evaluator 는 default 를 일절 author 하지 않으므로 reference 의 timeSamples /
        # OmniGraph PushGraph 가 master 타임라인을 따라 자유롭게 평가된다.
        #
        # 활성/비활성 트리거:
        #   - `begin_replay_mode(prim)`  : add  (TIMESAMPLES_REPLAY step Run 시작).
        #   - step 종료 시점에서는 끄지 않는다 — 사용자가 "Run 끝나면 그 자리에 그대로
        #     있어야" 라고 요구. evaluator 가 마지막 vt 의 값을 계속 default 로 박아
        #     viewport 가 끝 자세를 유지한다.
        #   - `end_replay_mode(prim)` : discard + 해당 prim 의 inst sublayer 에 박혀 있던
        #     default opinion 을 청소 (Reset 버튼에서만 호출).
        self._evaluator_active_prims: Set[str] = set()
        # TimeSamples 평가 시 timeCode 를 정수 프레임으로 맞출지 (Euler 보간 튐 완화).
        # 기본 ON — LAM Window 인스턴스 목록 헤더 체크박스로 즉시 토글.
        self._snap_timecode_to_frame: bool = True

    # ------------------------------------------------------------------ wiring

    def set_master(self, master) -> None:
        """LAM Window 가 호출. 이후 reauthor 가 master stage 에 author 한다."""
        self._master = master

    def set_global_speed(self, factor: float) -> None:
        """Phase 4 — 모든 인스턴스에 곱해지는 추가 속도 계수."""
        try:
            self._global_speed = max(0.01, float(factor))
        except Exception:
            self._global_speed = 1.0

    def get_global_speed(self) -> float:
        return self._global_speed

    def set_use_option_e(self, enabled: bool) -> None:
        """Phase B-1+ — Option E 경로 runtime toggle.

        검증 / 회귀 격리 / 비교 디버깅 용도. Kit 콘솔에서:
            evaluator.set_use_option_e(False)  # 핫픽스 6-10 경로로 즉시 복귀
            evaluator.set_use_option_e(True)   # 옵션 E 경로로 즉시 복귀

        False → True 전환: 다음 update tick 부터 runtime 객체 lazy create + freeze
        author 시작.
        True → False 전환: 다음 update tick 부터 핫픽스 6-10 경로 사용. 기존 runtime
        / freeze 캐시는 유지(다시 True 로 돌릴 때 재사용).
        """
        prev = self._RUNTIME_USE_OPTION_E
        self._RUNTIME_USE_OPTION_E = bool(enabled)
        if prev != self._RUNTIME_USE_OPTION_E:
            print(
                f"{_PRINT_PREFIX} OPTION_E toggle prev={prev} now={self._RUNTIME_USE_OPTION_E}",
                flush=True,
            )

    def get_use_option_e(self) -> bool:
        """현재 Option E 경로 활성 여부."""
        return self._RUNTIME_USE_OPTION_E

    def set_snap_timecode_to_frame(self, enabled: bool) -> None:
        """timeSamples / Option E 평가 시 ``round(vt * LAM_FIXED_FPS)`` 로 정수 timeCode 만 사용."""
        self._snap_timecode_to_frame = bool(enabled)

    def get_snap_timecode_to_frame(self) -> bool:
        return self._snap_timecode_to_frame

    def dump_option_e_state(self) -> str:
        """Option E 운영 상태를 콘솔에 한꺼번에 print + 같은 문자열로 반환.

        Kit UI 의 "Option E 진단" 버튼 또는 콘솔 호출용. 한 호출로 다음을 출력:
        - flag 상태, master stage 존재, root layer 식별자
        - registry 의 모든 instance 의 prim_path / state / virtual_time / source_asset
        - runtime 보유 여부, offscreen asset, is_ready, attribute 캐시 크기, 마지막 wrote
        - freeze 표식 보유 여부
        """
        lines = []
        try:
            stage = None
            if self._master is not None:
                try:
                    stage = self._master.get_stage()
                except Exception:
                    stage = None
            try:
                root_id = stage.GetRootLayer().identifier if stage else "<no_stage>"
            except Exception:
                root_id = "?"
            lines.append(
                f"flag={self._RUNTIME_USE_OPTION_E} master_stage_ok={stage is not None} "
                f"root_layer={root_id} update_sub={'set' if self._update_sub is not None else 'None'}"
            )
            for inst in self._registry.all_instances():
                rt = self._runtime_by_path.get(inst.prim_path)
                rt_part = (
                    "runtime=None"
                    if rt is None
                    else (
                        f"runtime(set offscreen={rt.offscreen_asset_path!r} "
                        f"ready={rt.is_ready} attrs_cached={len(rt._attr_cache)} "
                        f"last_wrote={rt.last_wrote} last_vt={rt.last_virtual_time})"
                    )
                )
                lines.append(
                    f"prim={inst.prim_path} state={inst.state} vt={inst.virtual_time:.3f}s "
                    f"src={getattr(inst, 'source_asset', '')!r} "
                    f"freeze={'yes' if inst.prim_path in self._option_e_freeze_seen else 'no'} "
                    f"{rt_part}"
                )
        except Exception as exc:
            lines.append(f"dump_option_e_state EXC: {exc}")
        text = " | ".join(lines)
        print(f"{_PRINT_PREFIX} DUMP {text}", flush=True)
        return text

    def reset_option_e_diag(self, prim_path: str = "") -> None:
        """Option E 의 per-instance 1회 진단 플래그를 reset 하여 다음 frame 에 다시 출력.

        `[모두 초기화]` 또는 `scheduler.start()` (RUN) 시 호출. dispose 와 달리 runtime
        객체와 offscreen stage 는 그대로 유지(성능 보호).

        Args:
            prim_path: 빈 문자열이면 모든 runtime 의 진단 플래그 reset, 그 외엔 해당 prim 만.
        """
        targets = (
            list(self._runtime_by_path.items())
            if not prim_path
            else [(prim_path, self._runtime_by_path.get(prim_path))]
        )
        for pp, rt in targets:
            if rt is None:
                continue
            for k in (
                "_diag_not_ready_logged",
                "_diag_first_call_logged",
                "_diag_first_eval_logged",
                "_diag_first_eval_warned_zero",
            ):
                try:
                    setattr(rt, k, False)
                except Exception:
                    pass
            print(
                f"{_PRINT_PREFIX} reset diag flags prim={pp} "
                f"(다음 update tick 에 진단 로그가 다시 출력됩니다)",
                flush=True,
            )

    def attach_memory_baked_layer(
        self,
        prim_path: str,
        baked_layer: Any,
        *,
        source_asset_for_log: str = "",
        mirror_asset_path_hint: str = "",
    ) -> bool:
        """**X3 in-memory bake 적용** — 지정 prim 의 runtime 의 offscreen Stage 를 baked layer 로 교체.

        ``lam_bake_omnigraph.bake_prim_to_timesamples_async(output_mode='memory')`` 가
        반환한 anonymous Sdf.Layer 를 받아, 해당 인스턴스 runtime 의 offscreen Stage 를
        이 layer 기반으로 재구성한다. 디스크에 baked.usd 를 만들지 않고도 standalone
        timeSamples 평가가 가능해진다.

        흐름:
            1) runtime_by_path[prim_path] 의 runtime 객체를 찾는다 (없으면 False).
            2) ``setup_offscreen_stage_from_layer`` 호출.
            3) attr_cache 무효화 + diag flag reset → 다음 evaluate 에서 새 stage 기준
               재빌드.

        Args:
            prim_path: master 의 인스턴스 prim path.
            baked_layer: ``pxr.Sdf.Layer``. anonymous 권장.
            source_asset_for_log: 로그 라벨에만 사용되는 원본 자산 경로 (선택).

        Returns:
            성공 여부.
        """
        if not prim_path:
            return False
        rt = self._runtime_by_path.get(prim_path)
        if rt is None:
            # Lazy create — update tick 이 한 번도 안 돌았다면 runtime 객체가 아직
            # 없을 수 있다. 이때는 registry 의 인스턴스 + master stage 로 즉시 생성.
            inst = None
            try:
                for it in self._registry.all_instances():
                    if it.prim_path == prim_path:
                        inst = it
                        break
            except Exception:
                inst = None
            if inst is None:
                print(
                    f"{_PRINT_PREFIX} attach_memory_baked_layer SKIP no runtime and "
                    f"no registry instance prim={prim_path}",
                    flush=True,
                )
                return False
            master_stage = None
            try:
                if self._master is not None:
                    master_stage = self._master.get_stage()
            except Exception:
                master_stage = None
            rt = AnimationInstanceRuntime(inst, master_stage=master_stage)
            self._runtime_by_path[prim_path] = rt
            try:
                rt.setup_master_mirror_prim()
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} attach_memory_baked_layer lazy mirror_prim FAIL "
                    f"prim={prim_path}: {exc}",
                    flush=True,
                )
            print(
                f"{_PRINT_PREFIX} attach_memory_baked_layer lazy-created runtime "
                f"prim={prim_path}",
                flush=True,
            )
        ok = False
        try:
            ok = bool(
                rt.setup_offscreen_stage_from_layer(
                    baked_layer,
                    asset_path=source_asset_for_log,
                    mirror_asset_path_hint=mirror_asset_path_hint,
                )
            )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} attach_memory_baked_layer EXC prim={prim_path}: {exc}",
                flush=True,
            )
            return False
        if not ok:
            return False
        # bake 결과를 새로 attach 했으니 OmniGraph deactivate 표식과 path 캐시도 reset →
        # 다음 update tick 에 다시 author 되어 baked timeSamples 가 winner 가 된다.
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        self._option_e_omnigraph_paths.pop(prim_path, None)
        # attr_cache + diag reset → 다음 evaluate 시 재빌드 + 진단 새로 출력.
        try:
            rt._attr_cache = []  # type: ignore[attr-defined]
            rt._attr_cache_built = False  # type: ignore[attr-defined]
        except Exception:
            pass
        for k in (
            "_diag_not_ready_logged",
            "_diag_first_call_logged",
            "_diag_first_eval_logged",
            "_diag_first_eval_warned_zero",
        ):
            try:
                setattr(rt, k, False)
            except Exception:
                pass
        # Q1 — 2026-05-12: bake 완료 표식을 인스턴스에 박는다. UI 가 이 값을 보고
        # [Bake] 버튼을 [BAKED ✓ / Re-bake] 로 전환한다. in-memory baked layer 는 휘발성
        # 이므로 Kit 재시작 시 자동으로 False 로 시작 (D13).
        try:
            target_inst = getattr(rt, "inst", None) or getattr(rt, "_instance", None)
            if target_inst is None:
                for it in self._registry.all_instances():
                    if it.prim_path == prim_path:
                        target_inst = it
                        break
            if target_inst is not None:
                target_inst.baked = True
        except Exception as _mark_exc:
            print(
                f"{_PRINT_PREFIX} attach_memory_baked_layer: baked flag 박기 실패 "
                f"prim={prim_path} exc={_mark_exc}",
                flush=True,
            )
        print(
            f"{_PRINT_PREFIX} attach_memory_baked_layer OK prim={prim_path} "
            f"src={source_asset_for_log!r} — attr_cache invalidated, "
            f"will rebuild next frame",
            flush=True,
        )
        return True

    def dump_master_timesamples_usda(self, prim_path: str):
        """**[Copy TS]** — timeSamples 를 USDA 텍스트로 직렬화 (클립보드용).

        우선순위 (2026-05-14 회귀 수정):
            1) **Option E offscreen stage** — in-memory bake / ``attach_memory_baked_layer``
               결과는 master mirror 에 default 로만 쓰이고, master ``Flatten`` 트리에는
               timeSamples 가 없을 수 있다. 따라서 ``rt._offscreen_stage`` 의
               ``GetRootLayer()`` 를 먼저 직렬화한다.
            2) 루트에 샘플이 없으면 ``offscreen_stage.Flatten()`` 결과 레이어를 시도
               (참조만 있는 자산 등).
            3) 그래도 없으면 기존처럼 **master** ``extract_subtree_to_anonymous_layer``
               (drag&drop / 파일 reference 의 master 트리).

        Returns:
            ``(ok, text, kind, result)`` 4-튜플.
        """
        import time as _time

        t0 = _time.perf_counter()
        from .lam_asset_diagnostics import _decide_kind
        from .lam_extract_from_master import (
            ExtractResult,
            dump_layer_to_usda_text,
            extract_subtree_to_anonymous_layer,
            scan_layer_timesample_stats,
        )
        from .lam_types import ASSET_KIND_UNKNOWN, AssetDiag

        def _stats_to_result(
            stats: dict,
            *,
            lyr: Any,
            discovered: str,
            data_source: str,
        ) -> ExtractResult:
            diag = AssetDiag(
                n_xform_op_ts=int(stats["n_xform_op_ts"]),
                n_skel_anim_ts=int(stats["n_skel_anim_ts"]),
                n_mesh_points_ts=int(stats["n_mesh_points_ts"]),
                n_visibility_ts=int(stats["n_visibility_ts"]),
                n_other_ts=int(stats["n_other_ts"]),
                n_omnigraph_prims=int(stats["n_omnigraph_prims"]),
                asset_start_tc=float(stats["tc_min"]),
                asset_end_tc=float(stats["tc_max"]),
            )
            try:
                kind = str(_decide_kind(diag)) or ASSET_KIND_UNKNOWN
            except Exception:
                kind = ASSET_KIND_UNKNOWN
            return ExtractResult(
                ok=True,
                root_prim_path=prim_path,
                layer=lyr,
                kind=kind,
                n_prims=int(stats["n_prims"]),
                n_attrs_total=int(stats["n_attrs_total"]),
                n_attrs_with_timesamples=int(stats["n_attrs_with_timesamples"]),
                n_xform_op_ts=int(stats["n_xform_op_ts"]),
                n_skel_anim_ts=int(stats["n_skel_anim_ts"]),
                n_mesh_points_ts=int(stats["n_mesh_points_ts"]),
                n_visibility_ts=int(stats["n_visibility_ts"]),
                n_other_ts=int(stats["n_other_ts"]),
                n_omnigraph_prims=int(stats["n_omnigraph_prims"]),
                tc_min=float(stats["tc_min"]),
                tc_max=float(stats["tc_max"]),
                elapsed_sec=float(_time.perf_counter() - t0),
                asset_label=data_source,
                discovered_asset_path=discovered or "",
            )

        # ------------------------------------------------------------------ 1) Offscreen
        rt = self._runtime_by_path.get(prim_path)
        if rt is not None:
            st = getattr(rt, "_offscreen_stage", None)
            if st is not None:
                discovered = ""
                try:
                    discovered = str(getattr(rt, "offscreen_asset_path", "") or "")
                except Exception:
                    discovered = ""

                candidates: list = []
                try:
                    rl = st.GetRootLayer()
                    if rl is not None:
                        candidates.append(("offscreen_root_layer", rl))
                except Exception:
                    pass
                try:
                    flat = st.Flatten()
                    if flat is not None:
                        candidates.append(("offscreen_stage.Flatten()", flat))
                except Exception:
                    pass

                seen: set = set()
                for src_name, lyr in candidates:
                    try:
                        lid = id(lyr)
                        if lid in seen:
                            continue
                        seen.add(lid)
                    except Exception:
                        pass
                    try:
                        stats = scan_layer_timesample_stats(lyr)
                    except Exception:
                        continue
                    if int(stats.get("n_attrs_with_timesamples", 0) or 0) <= 0:
                        continue

                    result = _stats_to_result(
                        stats,
                        lyr=lyr,
                        discovered=discovered,
                        data_source=src_name,
                    )
                    header = [
                        "# ============================================================",
                        "# LAM TimeSamples Dump",
                        f"# prim_path           = {prim_path}",
                        f"# data_source         = {src_name}",
                        f"# offscreen_asset     = {discovered or '(none)'}",
                        f"# kind                = {result.kind}",
                        f"# prims               = {result.n_prims}",
                        f"# attrs_total         = {result.n_attrs_total}",
                        f"# attrs_with_timeSamples = {result.n_attrs_with_timesamples}",
                        f"#   xform={result.n_xform_op_ts} skel={result.n_skel_anim_ts} "
                        f"mesh={result.n_mesh_points_ts} vis={result.n_visibility_ts} "
                        f"other={result.n_other_ts}",
                        f"# omnigraph_prims     = {result.n_omnigraph_prims}",
                        f"# timecode range      = [{result.tc_min:.3f}, {result.tc_max:.3f}]",
                        f"# elapsed_sec         = {result.elapsed_sec:.3f}",
                        "# ============================================================",
                    ]
                    try:
                        text = dump_layer_to_usda_text(lyr, header_lines=header)
                    except Exception as exc:
                        text = "\n".join([*header, f"# error: dump failed: {exc}"])
                        print(
                            f"{_PRINT_PREFIX} dump_master_timesamples FAIL serialize "
                            f"prim={prim_path} src={src_name} exc={exc}",
                            flush=True,
                        )
                        return (False, text, result.kind, result)

                    print(
                        f"{_PRINT_PREFIX} dump_master_timesamples OK prim={prim_path} "
                        f"src={src_name} {result.to_log_line()} text_bytes={len(text)}",
                        flush=True,
                    )
                    return (True, text, result.kind, result)

        # ------------------------------------------------------------------ 2) Master subtree
        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        if stage is None:
            r = ExtractResult(
                root_prim_path=prim_path,
                error="master stage is None — dump 불가",
                kind="UNKNOWN",
            )
            print(f"{_PRINT_PREFIX} dump_master_timesamples FAIL: {r.error}", flush=True)
            return (False, f"# error: {r.error}", "UNKNOWN", r)

        tag = prim_path.split("/")[-1] if prim_path else "anon"
        result = extract_subtree_to_anonymous_layer(stage, prim_path, tag_hint=tag)

        header = [
            "# ============================================================",
            "# LAM TimeSamples Dump",
            f"# prim_path           = {prim_path}",
            f"# data_source         = master_flatten_subtree",
            f"# discovered_asset    = {getattr(result, 'discovered_asset_path', '') or '(none)'}",
            f"# kind                = {result.kind}",
            f"# prims               = {result.n_prims}",
            f"# attrs_total         = {result.n_attrs_total}",
            f"# attrs_with_timeSamples = {result.n_attrs_with_timesamples}",
            f"#   xform={result.n_xform_op_ts} skel={result.n_skel_anim_ts} "
            f"mesh={result.n_mesh_points_ts} vis={result.n_visibility_ts} "
            f"other={result.n_other_ts}",
            f"# omnigraph_prims     = {result.n_omnigraph_prims}",
            f"# timecode range      = [{result.tc_min:.3f}, {result.tc_max:.3f}]",
            f"# extract elapsed     = {result.elapsed_sec:.3f}s",
            "# ============================================================",
        ]

        if result.n_attrs_with_timesamples <= 0:
            text = "\n".join(header)
            print(
                f"{_PRINT_PREFIX} dump_master_timesamples skip prim={prim_path} "
                f"kind={result.kind} (timeSamples=0, master+offscreen 모두 없음)",
                flush=True,
            )
            return (False, text, result.kind, result)

        try:
            text = dump_layer_to_usda_text(result.layer, header_lines=header)
        except Exception as exc:
            text = "\n".join([*header, f"# error: dump failed: {exc}"])
            print(
                f"{_PRINT_PREFIX} dump_master_timesamples FAIL serialize "
                f"prim={prim_path} exc={exc}",
                flush=True,
            )
            return (False, text, result.kind, result)

        print(
            f"{_PRINT_PREFIX} dump_master_timesamples OK prim={prim_path} "
            f"{result.to_log_line()} text_bytes={len(text)}",
            flush=True,
        )
        return (True, text, result.kind, result)

    def extract_and_attach_from_master(
        self,
        prim_path: str,
        *,
        source_asset_for_log: str = "",
    ):
        """**[Extract] 신규 path** (2026-05-13) — master 의 prim_path 하위 트리를 직접 추출.

        사용자가 ``/World/<인스턴스>`` 하위에 자산을 **drag&drop** 으로 직접 넣은 경우
        ``add_usd`` 가 박은 reference 와는 다른 형태가 된다 (Kit drop handler 가 자식
        prim 으로 reference 박음). 이 경우 본 메서드를 호출하면:

          1) ``master_stage.Flatten()`` 으로 모든 composition 평가.
          2) ``Sdf.CopySpec`` 으로 ``prim_path`` 하위 트리만 anonymous layer 의 ``/Root``
             아래로 복사.
          3) 그 anonymous layer 를 ``attach_memory_baked_layer`` 로 같은 인스턴스
             runtime 의 offscreen stage 에 attach → TIMESAMPLES_REPLAY 그대로 동작.

        본 메서드는 **기존 ``attach_memory_baked_layer`` 를 그대로 활용** 하며, bake
        와는 독립된 신규 path 다. bake 흐름 / TIMESAMPLES_REPLAY 흐름 모두 변경하지
        않는다.

        Returns:
            ``lam_extract_from_master.ExtractResult`` — ``ok`` / ``layer`` / 통계 포함.
        """
        from .lam_extract_from_master import (
            ExtractResult,
            extract_subtree_to_anonymous_layer,
        )

        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        if stage is None:
            r = ExtractResult(
                root_prim_path=prim_path,
                error="master stage 가 None — extract 불가",
            )
            print(f"{_PRINT_PREFIX} extract FAIL: {r.error}", flush=True)
            return r

        tag = prim_path.split("/")[-1] if prim_path else "anon"
        result = extract_subtree_to_anonymous_layer(stage, prim_path, tag_hint=tag)
        if not result.ok or result.layer is None:
            print(
                f"{_PRINT_PREFIX} extract FAIL prim={prim_path} "
                f"error={result.error!r} stats={result.to_log_line()}",
                flush=True,
            )
            return result

        # 2026-05-14 — attach 전에 inst.source_asset 을 result.discovered_asset_path 로
        # 미리 갱신해, attach 안에서 호출되는 ``sync_mirror_root_prim_path_from_master``
        # 가 drag&drop 자산 루트(``/World/inst/test1/N_07...``)를 정확히 인식하도록 한다.
        # (window 핸들러도 추가로 갱신하지만, 시점이 attach 이후이므로 첫 attr_cache 빌드
        # 시 너무 늦다 — 그래서 여기서 한 번 더 박는다.)
        discovered_for_attach = ""
        try:
            from .lam_extract_from_master import normalize_asset_uri_to_path

            discovered_for_attach = normalize_asset_uri_to_path(
                (getattr(result, "discovered_asset_path", "") or "").strip()
            )
            if discovered_for_attach:
                inst_obj = None
                try:
                    for it in self._registry.all_instances():
                        if it.prim_path == prim_path:
                            inst_obj = it
                            break
                except Exception:
                    inst_obj = None
                if inst_obj is not None and not (
                    getattr(inst_obj, "source_asset", "") or ""
                ).strip():
                    inst_obj.source_asset = discovered_for_attach
                    print(
                        f"{_PRINT_PREFIX} extract: inst.source_asset 사전 갱신 "
                        f"prim={prim_path} -> {discovered_for_attach}",
                        flush=True,
                    )
        except Exception as _src_exc:
            print(
                f"{_PRINT_PREFIX} extract: source_asset 사전 갱신 실패 "
                f"prim={prim_path}: {_src_exc}",
                flush=True,
            )

        # bake 결과 attach 와 100% 동일 경로 — runtime 의 offscreen_stage 가 새 layer 로
        # 재구성되고 attr_cache invalidate / inst.baked=True 표식까지 자동 처리된다.
        try:
            attach_ok = self.attach_memory_baked_layer(
                prim_path,
                result.layer,
                source_asset_for_log=source_asset_for_log
                or "<extracted-from-master>",
                mirror_asset_path_hint=discovered_for_attach,
            )
        except Exception as exc:
            result.ok = False
            result.error = f"attach_memory_baked_layer 예외: {exc}"
            print(
                f"{_PRINT_PREFIX} extract attach EXC prim={prim_path} exc={exc}",
                flush=True,
            )
            return result

        if not attach_ok:
            result.ok = False
            result.error = "attach_memory_baked_layer returned False (runtime 없음?)"
            print(
                f"{_PRINT_PREFIX} extract attach FAIL prim={prim_path}",
                flush=True,
            )
            return result

        print(
            f"{_PRINT_PREFIX} extract+attach OK prim={prim_path} "
            f"{result.to_log_line()} (TIMESAMPLES_REPLAY 즉시 사용 가능)",
            flush=True,
        )
        return result

    def force_rebuild_attr_cache(self, prim_path: str = "") -> None:
        """Option E 의 attribute 캐시를 강제로 재빌드하도록 표식만 비움.

        다음 `evaluate_and_write` 호출 시 `_build_attr_cache` 가 다시 호출되어 `cache map`
        / `cache built` / 0 이면 `diag dump offscreen` 진단이 다시 출력된다.

        Args:
            prim_path: 빈 문자열이면 모든 runtime, 그 외엔 해당 prim 만.
        """
        targets = (
            list(self._runtime_by_path.items())
            if not prim_path
            else [(prim_path, self._runtime_by_path.get(prim_path))]
        )
        for pp, rt in targets:
            if rt is None:
                continue
            try:
                rt._attr_cache = []  # type: ignore[attr-defined]
                rt._attr_cache_built = False  # type: ignore[attr-defined]
                print(
                    f"{_PRINT_PREFIX} attr_cache invalidated prim={pp} (will rebuild next frame)",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} attr_cache invalidate FAIL prim={pp}: {exc}",
                    flush=True,
                )

    def _resolve_instance_asset_path(self, inst: AnimationInstance) -> str:
        """REQ-005 P-2 상대 경로를 디스크 절대 경로로 보정.

        Registry 에 저장된 `source_asset` 은 master 저장 시 상대 경로일 수 있다.
        `Usd.Stage.Open` 은 실행 디렉터리가 아닐 때 실패하므로 master.usd 위치 기준으로
        조합해 재시도한다.
        """
        raw = (getattr(inst, "source_asset", "") or "").strip()
        if not raw:
            return ""
        raw_norm = raw.replace("\\", "/")
        try:
            if os.path.isfile(raw_norm):
                return os.path.normpath(os.path.abspath(raw_norm))
            mp = ""
            anon = True
            if self._master is not None:
                try:
                    mp = str(getattr(self._master, "master_path", "") or "")
                    anon = bool(getattr(self._master, "is_anonymous", True))
                except Exception:
                    mp, anon = "", True
            if mp and not anon:
                cand = os.path.normpath(
                    os.path.join(os.path.dirname(os.path.abspath(mp)), raw_norm)
                )
                if os.path.isfile(cand):
                    return cand
            cand_cwd = os.path.normpath(os.path.abspath(raw_norm))
            if os.path.isfile(cand_cwd):
                return cand_cwd
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} resolve asset_path failed prim={inst.prim_path} raw={raw!r}: {exc}",
                flush=True,
            )
        return raw_norm

    def invalidate_attr_cache(self, prim_path: Optional[str] = None) -> None:
        if prim_path is None:
            self._reauthor.invalidate_all()
        else:
            self._reauthor.invalidate(prim_path)

    def invalidate_mapping(self, prim_path: Optional[str] = None) -> None:
        """Hotfix6 — 외부에서 inst.virtual_time 을 직접 seek 한 경우 호출.

        다음 update tick 에서 _sync_layer_offset_mapping 이 LayerOffset 을 새로 author.
        """
        if prim_path is None:
            self._last_mapping_sig.clear()
            try:
                self._src_ref_tmpl_cache.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
        else:
            self._last_mapping_sig.pop(prim_path, None)
            try:
                self._src_ref_tmpl_cache.pop(prim_path, None)  # type: ignore[attr-defined]
            except Exception:
                pass

    def forget_instance(self, prim_path: str) -> None:
        """Hotfix7 — 인스턴스 unregister 시 evaluator 측 캐시 청소.

        (lam_multi_usd_loader.remove_usd 가 직접 호출하지 않더라도, registry listener 가
        notify 되어 호출되도록 lam_window 에서 연결한다.)
        """
        self._last_mapping_sig.pop(prim_path, None)
        try:
            self._src_ref_tmpl_cache.pop(prim_path, None)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self._diag_sublayer_authored_seen.discard(prim_path)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self._reauthor.invalidate(prim_path)
        except Exception:
            pass
        # Phase B-1 — Option E runtime 정리 (flag 와 무관하게 dispose 안전).
        rt = self._runtime_by_path.pop(prim_path, None)
        if rt is not None:
            try:
                rt.dispose()
            except Exception as exc:  # pragma: no cover - dispose 는 best-effort
                print(
                    f"{_PRINT_PREFIX} runtime.dispose failed prim={prim_path}: {exc}",
                    flush=True,
                )
        # Phase B-2-a — Option E freeze 표식 청소 (instance 재등록 시 다시 author 가능).
        self._option_e_freeze_seen.discard(prim_path)
        self._master_timeline_prims.discard(prim_path)
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        self._option_e_omnigraph_paths.pop(prim_path, None)
        self._bake_in_progress_prims.discard(prim_path)
        self._evaluator_active_prims.discard(prim_path)

    # ---------------------------------------------------- OmniGraph deactivate
    #
    # TIMESAMPLES_REPLAY 모드에서 master stage 안의 OmniGraph (예: `/World/aaa/PushGraph`)
    # 가 매 frame xformOp:* 를 push 하여 LAM 의 reauthor 결과(=baked timeSamples 값)
    # 를 덮어쓰는 문제 해결. instance 전용 sublayer 에 `over { active = false }` 만
    # author 하므로 사용자 master USD 는 무변경(session sublayer 라 휘발).
    #
    # USD_TIMELINE (TBS) 테스트 모드 진입 시에는 다시 active = True 로 author —
    # OmniGraph 가 master omni.timeline 시각으로 평가되어야 한다.

    _OMNIGRAPH_TYPE_NAMES: Tuple[str, ...] = (
        "OmniGraph",
        "OmniGraphNode",
        "PushGraph",
        "PushGraphNode",
    )

    def _collect_omnigraph_paths(self, stage, prim_path: str) -> list:
        """master stage 의 인스턴스 prim 산하에서 OmniGraph 류 prim 의 path 목록을 수집."""
        if stage is None or _Usd is None:
            return []
        try:
            prim = stage.GetPrimAtPath(prim_path)
        except Exception:
            return []
        if not prim or not prim.IsValid():
            return []
        out: list = []
        try:
            for p in _Usd.PrimRange(prim):
                try:
                    tn = str(p.GetTypeName() or "")
                except Exception:
                    continue
                if tn in self._OMNIGRAPH_TYPE_NAMES:
                    try:
                        out.append(str(p.GetPath()))
                    except Exception:
                        continue
        except Exception:
            return out
        return out

    def _set_omnigraph_active_in_sublayer(
        self, stage, prim_path: str, *, active: bool
    ) -> int:
        """instance 전용 sublayer 안에 OmniGraph prim 들의 active flag 를 author.

        Returns:
            author 한 spec 개수.
        """
        if stage is None or self._master is None or _Sdf is None:
            return 0
        try:
            sublayer = self._master.ensure_inst_sublayer(prim_path, tag_hint=prim_path)
        except Exception:
            sublayer = None
        if sublayer is None:
            return 0
        paths = self._option_e_omnigraph_paths.get(prim_path)
        if paths is None:
            paths = self._collect_omnigraph_paths(stage, prim_path)
            self._option_e_omnigraph_paths[prim_path] = paths
        if not paths:
            return 0
        n = 0
        try:
            with _Sdf.ChangeBlock():
                for p in paths:
                    try:
                        spec = sublayer.GetPrimAtPath(p)
                        if spec is None:
                            spec = _Sdf.CreatePrimInLayer(sublayer, _Sdf.Path(p))
                        if spec is None:
                            continue
                        try:
                            spec.specifier = _Sdf.SpecifierOver
                        except Exception:
                            pass
                        spec.active = bool(active)
                        n += 1
                    except Exception:
                        continue
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} omnigraph active({active}) toggle FAIL "
                f"prim={prim_path}: {exc}",
                flush=True,
            )
            return 0
        return n

    def begin_bake_mode(self, prim_path: str) -> bool:
        """[Bake] 시작 시 호출 — 해당 prim 의 모든 Option E 자동 author 를 보류한다.

        - LayerOffset 을 (0, 1) 로 author → master timeline scrub 이 reference 의
          timeSamples / OmniGraph 평가를 자유롭게 받을 수 있도록 한다.
        - master sublayer 의 OmniGraph ``over { active=false }`` 를 ``active=True`` 로
          토글 → bake 의 scrub 단계에서 PushGraph 가 평가되어 timeSamples 가 박힌다.
        - `_bake_in_progress_prims` 표식 → 매 update tick 의 ``_ensure_option_e_freeze``
          / ``_ensure_option_e_omnigraph_deactivated`` 가 본 prim 에 대해 skip.

        `end_bake_mode` 가 호출되면 표식 해제 + freeze/deactivate 표식 reset →
        다음 update tick 에서 자연스럽게 TIMESAMPLES_REPLAY 모드로 자동 복귀.
        """
        if not prim_path or not prim_path.startswith("/"):
            return False
        # drag&drop 후 구성이 바뀌었는데 이전 세션의 ref 템플릿(인스턴스 루트 + source_asset)
        # 이 캐시에 남아 있으면, bake 직전 `_set_prim_layer_offset` 이 **인스턴스 prim 에
        # 전체 자산 reference 를 한 번 더** 박아 viewport 에 자산이 이중으로 보인다.
        try:
            tmpl = getattr(self, "_src_ref_tmpl_cache", None)
            if isinstance(tmpl, dict):
                tmpl.pop(prim_path, None)
        except Exception:
            pass
        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        if stage is None:
            print(
                f"{_PRINT_PREFIX} begin_bake_mode FAIL no stage prim={prim_path}",
                flush=True,
            )
            return False
        self._bake_in_progress_prims.add(prim_path)
        self._option_e_freeze_seen.discard(prim_path)
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        ok_off = self._set_prim_layer_offset(
            stage, prim_path, offset=0.0, scale=1.0
        )
        n_act = self._set_omnigraph_active_in_sublayer(
            stage, prim_path, active=True
        )
        print(
            f"{_PRINT_PREFIX} begin_bake_mode prim={prim_path} "
            f"ref_layer_offset=(0,1) ok={ok_off} omnigraph_reactivated={n_act} "
            f"(freeze/deactivate 자동 author 보류)",
            flush=True,
        )
        return True

    def end_bake_mode(self, prim_path: str) -> None:
        """[Bake] 종료 시 호출 — 다음 update tick 에서 freeze/deactivate 자동 복귀."""
        was_in = prim_path in self._bake_in_progress_prims
        self._bake_in_progress_prims.discard(prim_path)
        self._option_e_freeze_seen.discard(prim_path)
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        print(
            f"{_PRINT_PREFIX} end_bake_mode prim={prim_path} was_in={was_in} "
            f"(다음 update tick 에서 freeze/OmniGraph deactivate 자동 복귀)",
            flush=True,
        )

    def set_omnigraph_active_for_instance(
        self, prim_path: str, active: bool
    ) -> int:
        """외부(bake) 가 호출 — instance sublayer 의 OmniGraph active flag toggle.

        bake 시 master stage 의 OmniGraph 가 평가되어야 baked timeSamples 가 생성된다.
        TIMESAMPLES_REPLAY 모드에서는 우리가 OmniGraph 를 비활성화해 둔 상태이므로,
        bake 시작 직전 ``active=True`` 로 잠시 활성, bake 완료 후 attach 호출에서
        표식이 reset 되어 다음 update tick 에서 자동으로 다시 비활성화된다.

        Args:
            prim_path: 대상 인스턴스의 master prim path.
            active: True 면 OmniGraph spec 들의 active=True 로 author, False 면 False.

        Returns:
            author 한 spec 개수. master/stage/Sdf 가 없거나 OmniGraph prim 이 없으면 0.
        """
        if not prim_path or not prim_path.startswith("/"):
            return 0
        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        if stage is None:
            return 0
        n = self._set_omnigraph_active_in_sublayer(
            stage, prim_path, active=bool(active)
        )
        if active:
            self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        elif n > 0:
            self._option_e_omnigraph_deactivated_seen.add(prim_path)
        if n > 0:
            paths = self._option_e_omnigraph_paths.get(prim_path, [])
            print(
                f"{_PRINT_PREFIX} set_omnigraph_active_for_instance prim={prim_path} "
                f"active={active} count={n} paths={paths[:8]}",
                flush=True,
            )
        return n

    def _ensure_option_e_omnigraph_deactivated(
        self, prim_path: str, stage
    ) -> None:
        """no-op (2026-05-12 사용자 요청) — master 타임라인 자유 재생을 위해 비활성화.

        과거에는 TIMESAMPLES_REPLAY 가 마스킹되지 않도록 인스턴스 산하 OmniGraph prim 을
        LAM sublayer 에서 ``over { active = false }`` 로 author 했지만, 그 결과 USD 추가
        직후 master 타임라인을 돌려도 prim 이 움직이지 않는 부작용이 있었다. 사용자가
        "타임라인 재생을 막는 코드를 모두 제거" 를 요청 → 본 함수는 의도적으로 비워둔다.

        호출 사이트(`_on_update_option_e`, `end_master_timeline_mode`) 는 그대로 둔다 —
        나중에 multi-instance 독립 재생이 다시 필요해질 때 본 함수만 되살리면 된다.
        """
        return

    # ------------------------------------------------------------------ replay mode (per-step freeze)

    def begin_replay_mode(self, prim_path: str) -> bool:
        """TIMESAMPLES_REPLAY step **시작** — evaluator default 쓰기 + OmniGraph 비활성.

        2026-05-13 사용자 합의 정정:
        - 평상시(USD 로드 직후) 의 **자동** freeze / OmniGraph deactivate 는 제거.
          `_ensure_option_e_freeze` / `_ensure_option_e_omnigraph_deactivated` 가 no-op.
        - 단 TIMESAMPLES_REPLAY step 이 활성인 동안에는 OmniGraph PushGraph 가 Fabric
          으로 xformOp 값을 직접 push 하여 evaluator 의 default 를 viewport 에서 가리는
          문제가 있어, step-scoped 로만 OmniGraph 를 비활성화한다. USD value resolution
          상 sublayer 의 default 가 winner 이지만 Hydra 가 Fabric 의 OmniGraph push
          값을 우선 렌더링하므로 evaluator 의 결과가 안 보이는 회귀를 일으킴 (2026-05-13
          사용자 보고: `bake 후 timesamples replay 가 또 안 됨`).
        - step 종료 시점에서는 끄지 **않는다** — 사용자 요구 "Run 끝나면 그 자리에
          그대로 있어야". evaluator 가 last vt 의 default 를 계속 author + OmniGraph 가
          계속 비활성 → 끝 자세를 잠금.
        - 명시적 Reset 만 ``end_replay_mode`` 를 호출 → OmniGraph 다시 활성 +
          inst sublayer default opinion 청소 → 평상시 자유 재생 상태로 복귀.
        """
        if not prim_path or not prim_path.startswith("/"):
            return False
        if prim_path in self._master_timeline_prims:
            # USD_TIMELINE 과 동시 활성이면 그쪽이 우선.
            return True
        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        added = prim_path not in self._evaluator_active_prims
        self._evaluator_active_prims.add(prim_path)
        n_de = 0
        if stage is not None:
            n_de = self._set_omnigraph_active_in_sublayer(
                stage, prim_path, active=False
            )
            if n_de > 0:
                self._option_e_omnigraph_deactivated_seen.add(prim_path)
        print(
            f"{_PRINT_PREFIX} TIMESAMPLES_REPLAY mode BEGIN prim={prim_path} "
            f"evaluator_active={'NEW' if added else 'ALREADY'} "
            f"omnigraph_deactivated={n_de} "
            f"(default 쓰기 활성 + OmniGraph step-scope 비활성)",
            flush=True,
        )
        return True

    def end_replay_mode(self, prim_path: str) -> None:
        """TIMESAMPLES_REPLAY 종료 — Reset 시에만 호출. evaluator default 청소 + OmniGraph 복원.

        - `_evaluator_active_prims` 에서 prim 제거 → 다음 update tick 부터 evaluator 가
          더 이상 default 를 author 하지 않는다.
        - 인스턴스 sublayer 의 prim spec 에 박힌 attribute default opinion 을 청소.
        - 인스턴스 sublayer 에 박혀 있던 OmniGraph ``over { active=false }`` 를
          ``active=true`` 로 다시 author → master 타임라인 슬라이더로 OmniGraph 가 다시
          자유 평가되어 viewport 가 timeline 을 따라간다.
        """
        if not prim_path or not prim_path.startswith("/"):
            return
        was_active = prim_path in self._evaluator_active_prims
        self._evaluator_active_prims.discard(prim_path)
        cleared = self._clear_inst_sublayer_attr_defaults(prim_path)
        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        n_act = 0
        if stage is not None:
            n_act = self._set_omnigraph_active_in_sublayer(
                stage, prim_path, active=True
            )
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        print(
            f"{_PRINT_PREFIX} TIMESAMPLES_REPLAY mode END prim={prim_path} "
            f"was_active={was_active} cleared_attrs={cleared} "
            f"omnigraph_reactivated={n_act}",
            flush=True,
        )

    def _clear_inst_sublayer_attr_defaults(self, prim_path: str) -> int:
        """인스턴스 sublayer 안의 attribute default opinion 을 **재귀적으로** 청소.

        Sdf.PrimSpec 안에 evaluator 가 박은 ``default = ...`` 만 제거하고 reference /
        payload 같은 compositional opinion 은 그대로 둔다. evaluator 의 Option E mirror
        author 는 inst_prim 그 자체뿐만 아니라 drag&drop 위치 (``.../test1/N_07_*/Geom/...``)
        하위 자식 prim 들에도 attribute default 와 OverridePrim spec 을 남기므로, Reset /
        재추출 / 재 Bake 직전 이 trash 들을 모두 비워야 master stage 가 깨끗해진다 (그렇지
        않으면 다음 attach 후 stage 트리에 "내부 자산이 한 단계 더 복제된 것처럼" 보임).

        반환: 제거된 attribute spec 의 수.
        """
        if self._master is None or _Usd is None or _Sdf is None:
            return 0
        try:
            inst_sublayer = self._master.get_inst_sublayer(prim_path)
        except Exception:
            inst_sublayer = None
        if inst_sublayer is None:
            return 0
        base = str(prim_path or "").rstrip("/")
        roots_to_walk: List[Any] = []
        try:
            root_spec = inst_sublayer.GetPrimAtPath(prim_path)
        except Exception:
            root_spec = None
        if root_spec is not None:
            roots_to_walk.append(root_spec)
        else:
            # drag&drop 전용 — LayerOffset author 가 인스턴스 prim 이 아니라
            # ``/World/aaa/test1`` 같은 자식 경로의 spec 에만 있을 수 있다.
            try:
                pseudo = inst_sublayer.pseudoRoot
                for _nm, ch in list(pseudo.nameChildren.items()):
                    try:
                        pth = str(ch.path)
                    except Exception:
                        continue
                    if pth == base or pth.startswith(base + "/"):
                        roots_to_walk.append(ch)
            except Exception:
                pass
        if not roots_to_walk:
            return 0

        cleared = 0
        # 자식 → 부모 순으로 비워야 빈 PrimSpec 을 RemovePrim 으로 안전 제거할 수 있다.
        ordered: List[Any] = []

        def _walk(spec: Any) -> None:
            try:
                ordered.append(spec)
                for ch in list(spec.nameChildren):
                    _walk(ch)
            except Exception:
                return

        try:
            for rs in roots_to_walk:
                _walk(rs)
        except Exception:
            return cleared

        # 첫 번째 root — root_spec 비교용 (인스턴스 prim spec 이 있으면 그걸 우선).
        root_spec = roots_to_walk[0]
        try:
            maybe_inst = inst_sublayer.GetPrimAtPath(prim_path)
            if maybe_inst is not None:
                root_spec = maybe_inst
        except Exception:
            pass

        # 후위 순회 (자식 먼저)
        for spec in reversed(ordered):
            try:
                attr_names = list(spec.attributes.keys())
            except Exception:
                attr_names = []
            for name in attr_names:
                try:
                    attr_spec = spec.attributes[name]
                    if attr_spec is None:
                        continue
                    spec.RemoveProperty(attr_spec)
                    cleared += 1
                except Exception:
                    continue
            # evaluator 가 만든 OverridePrim spec — attribute / 자식이 모두 사라지고
            # compositional opinion (reference / payload / typeName / specifier=def) 도
            # 없으면 빈 over spec 만 남는다. master 트리에 보이지 않도록 제거.
            try:
                has_children = bool(list(spec.nameChildren))
            except Exception:
                has_children = True
            try:
                has_attrs = bool(list(spec.attributes))
            except Exception:
                has_attrs = True
            if has_children or has_attrs:
                continue
            try:
                specifier = spec.specifier
            except Exception:
                specifier = None
            try:
                has_refs = bool(spec.referenceList.GetAddedOrExplicitItems()) if hasattr(spec, "referenceList") else False
            except Exception:
                has_refs = False
            try:
                has_payloads = bool(spec.payloadList.GetAddedOrExplicitItems()) if hasattr(spec, "payloadList") else False
            except Exception:
                has_payloads = False
            try:
                tn = str(spec.typeName) if hasattr(spec, "typeName") else ""
            except Exception:
                tn = ""
            is_over = False
            try:
                is_over = (specifier == _Sdf.SpecifierOver)
            except Exception:
                is_over = False
            if not is_over or has_refs or has_payloads or tn:
                continue
            # root_spec 자체는 master_stage 합성용 entry 라서 남겨둔다 — attribute 만 비운다.
            try:
                if spec is root_spec:
                    continue
            except Exception:
                pass
            try:
                parent = spec.nameParent
                if parent is not None:
                    parent.RemoveNameChild(spec)
            except Exception:
                try:
                    inst_sublayer.RemovePrim(spec.path)
                except Exception:
                    continue
        return cleared

    def begin_master_timeline_mode(self, prim_path: str) -> bool:
        """USD_TIMELINE (TBS 스타일) — evaluator default 쓰기를 끄고 omni.timeline 이 master
        stage 시각을 평가하도록 한다.

        2026-05-13 사용자 합의 (`타임라인을 막는 코드를 모두 제거`):
        - 평상시 freeze / OmniGraph deactivate override 가 author 되지 않으므로 본
          함수가 따로 "unfreeze" 를 author 할 필요가 없다 → `_set_prim_layer_offset` /
          `_set_omnigraph_active_in_sublayer` 호출 제거.
        - 추가로 이전에 TIMESAMPLES_REPLAY 가 활성화돼 evaluator 가 default 를 박고
          있던 prim 이라면, USD_TIMELINE step 진입 시 evaluator 의 default 가 master
          timeline 평가를 가리지 않도록 ``_evaluator_active_prims`` 에서 잠깐 빼고
          (USD_TIMELINE step 종료 시 ``_master_timeline_prims`` 에서 빠지므로 default
          authoring 을 다시 시작하지는 않는다 — 사용자 의도 = "TIMESAMPLES_REPLAY 만
          쓴다") inst sublayer 의 default opinion 도 청소한다.
        """
        if not prim_path or not prim_path.startswith("/"):
            return False
        self._master_timeline_prims.add(prim_path)
        self._evaluator_active_prims.discard(prim_path)
        cleared = self._clear_inst_sublayer_attr_defaults(prim_path)
        # 이전에 TIMESAMPLES_REPLAY 가 활성화돼 OmniGraph 가 비활성 상태일 수 있다.
        # USD_TIMELINE step 은 OmniGraph + timeline 평가에 의존하므로 active=True 로 복원.
        stage = None
        if self._master is not None:
            try:
                stage = self._master.get_stage()
            except Exception:
                stage = None
        n_act = 0
        if stage is not None:
            n_act = self._set_omnigraph_active_in_sublayer(
                stage, prim_path, active=True
            )
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        print(
            f"{_PRINT_PREFIX} USD_TIMELINE master mode BEGIN prim={prim_path} "
            f"cleared_default_attrs={cleared} omnigraph_reactivated={n_act}",
            flush=True,
        )
        return True

    def end_master_timeline_mode(
        self,
        prim_path: str,
        *,
        freeze_at_tc: Optional[float] = None,
    ) -> None:
        """USD_TIMELINE step 종료 — `_master_timeline_prims` 에서만 제외한다.

        2026-05-13 사용자 합의 (`타임라인을 막는 코드를 모두 제거`):
        - freeze (LayerOffset(end_tc, 1e-9)) 및 OmniGraph deactivate 재적용 일체 제거.
        - omni.timeline 이 step 종료 시점에 end_tc 에서 일시정지되어 있으면 그 자세가
          그대로 viewport 에 유지된다 (reference + OmniGraph 가 timeline 의 현재 시각을
          평가). 사용자가 timeline 슬라이더를 움직이면 prim 도 자유롭게 따라간다.
        - ``freeze_at_tc`` 인자는 호출 시그니처 호환을 위해 남겨둔다 (현재 무시).
        """
        self._master_timeline_prims.discard(prim_path)
        self._option_e_freeze_seen.discard(prim_path)
        self._option_e_omnigraph_deactivated_seen.discard(prim_path)
        print(
            f"{_PRINT_PREFIX} USD_TIMELINE master mode END prim={prim_path} "
            f"freeze_at_tc={freeze_at_tc!r} (override 일체 author 하지 않음)",
            flush=True,
        )

    # ------------------------------------------------------------------ start

    def start(self) -> None:
        if self._update_sub is not None:
            return
        try:
            import omni.kit.app as app  # type: ignore

            stream = app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(self._on_update, name="lam.evaluator")
            self._last_perf = time.perf_counter()
            # FPS 30 고정 — evaluator 가 시작될 때 master stage + omni.timeline 양쪽 강제.
            try:
                if self._master is not None:
                    fn = getattr(self._master, "force_fixed_fps_30", None)
                    if callable(fn):
                        fn()
            except Exception as exc:
                print(f"{_PRINT_PREFIX} master.force_fixed_fps_30 failed: {exc}", flush=True)
            print(f"{_PRINT_PREFIX} evaluator started (no omni.timeline)", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} start failed: {exc}", flush=True)
            self._update_sub = None

    def stop(self) -> None:
        if self._update_sub is None:
            return
        try:
            self._update_sub = None
            print(f"{_PRINT_PREFIX} evaluator stopped", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} stop failed: {exc}", flush=True)
        # Phase B-1 — Option E runtime 들 dispose (flag 무관, 안전 cleanup).
        for prim_path, rt in list(self._runtime_by_path.items()):
            try:
                rt.dispose()
            except Exception as exc:  # pragma: no cover - best-effort
                print(
                    f"{_PRINT_PREFIX} runtime.dispose failed prim={prim_path}: {exc}",
                    flush=True,
                )
        self._runtime_by_path.clear()
        # Phase B-2-a — re-start 후 freeze 가 다시 author 되도록 표식도 청소.
        self._option_e_freeze_seen.clear()

    # ----------------------------------------------------------------- update

    def _on_update(self, event) -> None:  # noqa: ARG002 — event 자체는 여기서 안 봄
        now = time.perf_counter()
        dt = now - self._last_perf
        if dt < 0.0:
            dt = 0.0
        self._last_perf = now
        # 한 번에 너무 큰 dt 는 인위적으로 잘라낸다(브레이크포인트 등).
        if dt > 0.25:
            dt = 0.25

        # 매 프레임 EditTarget 을 root layer 로 강제(다른 코드가 session 으로 바꾸어도 복구).
        stage = None
        if self._master is not None:
            try:
                self._master.set_root_layer_edit_target()
                stage = self._master.get_stage()
            except Exception:
                stage = None

        # Phase B-1 — Option E 분기. flag True 면 새 경로로 위임하고 옛 경로는 skip.
        # flag False (기본값) 면 본 분기는 건너뛰고 아래 핫픽스 6-10 경로가 그대로 실행.
        if self._RUNTIME_USE_OPTION_E:
            self._on_update_option_e(dt, stage)
            return

        # Phase 6 Hotfix6.2 — wall clock master_seconds 는 any_playing 인 frame 만 누적.
        # (모든 인스턴스가 stopped 일 때는 누적하지 않아 다음 RUN 의 master_tc 가 0 부터 시작.)
        any_playing_now = any(
            inst.state == "playing" for inst in self._registry.all_instances()
        )
        if any_playing_now:
            self._master_seconds += dt

        # Phase 6 Hotfix6 — playing 진입/종료/loop wrap/speed 변경 시점에만 layerOffset author.
        # 매 frame 인스턴스 전체 순회하지만 실제 author 는 시그니처 변경 시에만 일어난다.
        any_playing = any_playing_now
        if stage is not None and _Sdf is not None:
            tps_master = self._stage_tps(stage)
            master_tc = self._master_seconds * tps_master
            for inst in self._registry.all_instances():
                self._sync_layer_offset_mapping(inst, stage, master_tc=master_tc, tps_master=tps_master)

        last_playing_eval_seconds: Optional[float] = None
        last_playing_path: Optional[str] = None
        for inst in self._registry.all_instances():
            if inst.state != "playing":
                continue
            # 진단 — 인스턴스가 처음 playing 으로 보일 때 1회 print(이후 ticks 는 조용히).
            if not getattr(inst, "_lam_diag_seen_playing", False):
                try:
                    inst._lam_diag_seen_playing = True  # type: ignore[attr-defined]
                except Exception:
                    pass
                print(
                    f"{_PRINT_PREFIX} tick begin prim={inst.prim_path} "
                    f"asset=[{inst.asset_start_time},{inst.asset_end_time}]@{inst.asset_tps}fps "
                    f"range={inst.range} v_t={inst.virtual_time:.3f}s "
                    f"start_t={self._start_time_seconds(inst):.3f}s "
                    f"end_t={self._end_time_seconds(inst):.3f}s",
                    flush=True,
                )
            self._tick_instance(inst, dt, stage=stage)
            # (Hotfix6) last_playing_* 는 더 이상 stage time set 에 사용되지 않는다.
            # stage current time 은 wall clock master_seconds 로 매 frame 진행되며,
            # 각 인스턴스는 자기 reference 의 SdfLayerOffset 매핑으로 자기 inst_tc 로 평가된다.
            if inst.state == "playing":
                last_playing_eval_seconds = inst.virtual_time + float(inst.offset_sec)
                last_playing_path = inst.prim_path
            # 인스턴스가 한 번 stopped 로 돌아가면 다음 playing 진입 시 다시 1회 print 가능하도록.
            if inst.state != "playing":
                try:
                    inst._lam_diag_seen_playing = False  # type: ignore[attr-defined]
                except Exception:
                    pass

        # Phase 6 Hotfix6 — stage current time 은 evaluator 의 wall clock(master_seconds) 만 set.
        # (단일 stage 멀티 평가 한계 우회: 인스턴스마다 다른 SdfLayerOffset 매핑이 있어
        # 같은 master_tc 에서도 서로 다른 inst_tc 로 평가됨 = 진정한 independent playback.)
        # 모든 인스턴스가 stopped 면 stage time 을 건드리지 않아 사용자 슬라이더 조작을 보장.
        if self._use_omni_timeline and any_playing:
            self._advance_stage_time(stage, self._master_seconds, owner_prim="<wall_clock>")
        # 일순간 모두 stopped 가 되면 master_seconds 를 0 으로 reset (다음 재생을 깨끗한 상태에서 시작).
        if self._last_was_any_playing and not any_playing:
            self._master_seconds = 0.0
        self._last_was_any_playing = any_playing
        # diagnostic 로 마지막 playing 을 굳이 더 쓰지 않으나 변수 보존.
        _ = (last_playing_eval_seconds, last_playing_path)

    def _tick_instance(self, inst: AnimationInstance, dt: float, *, stage=None) -> None:
        """1 인스턴스 1 프레임."""
        prev_t = inst.virtual_time
        # Phase 4 — 인스턴스 자체 speed 에 evaluator 의 global_speed 가 곱해진다.
        sp = max(0.01, float(inst.speed)) * max(0.01, float(self._global_speed))
        new_t = prev_t + dt * sp

        end_t = self._end_time_seconds(inst)
        start_t = self._start_time_seconds(inst)

        completed = False
        looped_wrap = False
        if end_t > start_t and new_t >= end_t:
            if inst.loop:
                # 루프: 끝을 넘긴 만큼 다시 시작점에 더한다.
                length = end_t - start_t
                if length > 1e-6:
                    over = (new_t - start_t) % length
                    new_t = start_t + over
                else:
                    new_t = start_t
                looped_wrap = True
            else:
                new_t = end_t
                completed = True

        inst.virtual_time = new_t

        # Hotfix6 — loop wrap 발생 시 layerOffset mapping 을 무효화해야 화면도 같이 wrap.
        # (mapping 시그니처는 vt 에 의존하지 않아 wrap 만으로는 자동 변경되지 않음.)
        if looped_wrap:
            self._last_mapping_sig.pop(inst.prim_path, None)

        if self._verbose:
            print(
                f"{_PRINT_PREFIX} tick prim={inst.prim_path} "
                f"v_t={new_t:.3f}/{end_t:.3f}s sp={inst.speed}*{self._global_speed:.2f} "
                f"loop={inst.loop}",
                flush=True,
            )

        # Phase 2 — 실제 attribute reauthor.
        if stage is not None:
            try:
                wrote = self._reauthor.reauthor_at(
                    stage,
                    inst,
                    eval_seconds=new_t + float(inst.offset_sec),
                    snap_timecode_to_frame=self._snap_timecode_to_frame,
                )
                # 진단 — 인스턴스 첫 reauthor 결과 1회 print. wrote=0 이면 USD value resolution
                # 한계(=가설 B) 가능성. cache built 결과는 attribute_reauthor 모듈에서 별도로 출력됨.
                if not getattr(inst, "_lam_diag_reauthor_seen", False):
                    try:
                        inst._lam_diag_reauthor_seen = True  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    print(
                        f"{_PRINT_PREFIX} first reauthor prim={inst.prim_path} "
                        f"v_t={new_t:.3f}s tps={LAM_FIXED_FPS}(forced) "
                        f"timeCode={(new_t + float(inst.offset_sec)) * LAM_FIXED_FPS:.3f} "
                        f"wrote={wrote}",
                        flush=True,
                    )
                    if wrote == 0:
                        print(
                            f"{_PRINT_PREFIX} WARN reauthor wrote=0 — attribute timeSamples 가 "
                            f"없거나, USD value resolution 한계(reference 의 timeSamples 가 root layer "
                            f"의 default 보다 우선)일 수 있습니다. "
                            f"attribute_reauthor 의 'cache built attrs=N' 메시지가 N=0 이면 전자, "
                            f"N>0 인데도 화면에 변화 없으면 후자(가설 B).",
                            flush=True,
                        )
            except Exception as exc:
                print(f"{_PRINT_PREFIX} reauthor error prim={inst.prim_path}: {exc}", flush=True)

        if completed:
            inst.state = "stopped"
            print(f"{_PRINT_PREFIX} completed prim={inst.prim_path}", flush=True)

    # ------------------------------------------------------------- Option E (Phase B-1)

    def _on_update_option_e(self, dt: float, stage) -> None:
        """`_RUNTIME_USE_OPTION_E=True` 일 때 호출되는 새 경로 (Phase B-1).

        본 경로는 핫픽스 6-10 의 다음 우회 메커니즘을 **일절 사용하지 않는다**:
        - `omni.timeline.set_current_time` (stage current_time 진행)
        - `Sdf.LayerOffset` mapping author / sublayer override
        - `SetInstanceable(False)` / freeze scale 우회 / fps 강제

        대신 instance 마다 `AnimationInstanceRuntime` 1 개를 두고:
          1. 자기 자산 USD 를 offscreen Stage 로 open
          2. 매 frame `evaluate_and_write(virtual_time)` 호출 — offscreen 에서 평가한
             attribute 값을 master stage 의 동일 prim path attribute 의 default 로 author
        master stage 의 timeline 은 진행하지 않는다(default 가 reference 의 timeSamples
        보다 winner — value resolution 의 stronger root layer 규칙).

        Phase B-1 의 책임 범위:
        - flag=True 진입 시 새 경로의 entry point 구축 + runtime lifecycle 관리.
        - master stage 의 reference 안 timeSamples 가 stage current_time 평가로
          잘못 보이는 부분은 **본 경로의 책임 밖** — Phase B-2 (multi_usd_loader 변경,
          B-2-a 권장: LAM sublayer 안 LayerOffset(0, 1e-9) freeze 유지) 에서 해결.

        `_RUNTIME_USE_OPTION_E=False` 인 환경에서는 본 method 가 호출되지 않음.
        """
        if not self._diag_option_e_announced:
            self._diag_option_e_announced = True
            try:
                stage_ok = stage is not None
                root_id = "?"
                if stage_ok:
                    try:
                        root_id = stage.GetRootLayer().identifier
                    except Exception:
                        root_id = "?"
            except Exception:
                stage_ok = False
                root_id = "?"
            print(
                f"{_PRINT_PREFIX} OPTION_E path enabled (Phase B-1) — "
                f"hotfix 6-10 paths skipped | stage_ok={stage_ok} root_layer={root_id} "
                f"registry={len(self._registry.all_instances())}",
                flush=True,
            )

        # Per-instance setup 결과를 명확히 출력 (B-3 진단).
        for inst in self._registry.all_instances():
            rt = self._runtime_by_path.get(inst.prim_path)
            if rt is None:
                rt = AnimationInstanceRuntime(inst, master_stage=stage)
                self._runtime_by_path[inst.prim_path] = rt
                resolved = self._resolve_instance_asset_path(inst)
                if resolved:
                    ok_open = rt.setup_offscreen_stage(resolved)
                    if not ok_open and not rt._lam_option_e_setup_fail_logged:
                        rt._lam_option_e_setup_fail_logged = True
                        print(
                            f"{_PRINT_PREFIX} OPTION_E setup_offscreen_stage FAIL "
                            f"prim={inst.prim_path} resolved={resolved}",
                            flush=True,
                        )
                else:
                    print(
                        f"{_PRINT_PREFIX} OPTION_E source_asset EMPTY prim={inst.prim_path} "
                        f"raw={getattr(inst, 'source_asset', '')!r} master_path="
                        f"{getattr(self._master, 'master_path', '')!r}",
                        flush=True,
                    )
                rt.setup_master_mirror_prim()
            else:
                rt.set_master_stage(stage)
                if not rt.offscreen_asset_path and getattr(inst, "source_asset", ""):
                    resolved2 = self._resolve_instance_asset_path(inst)
                    if resolved2:
                        ok2 = rt.setup_offscreen_stage(resolved2)
                        if not ok2 and not rt._lam_option_e_setup_fail_logged:
                            rt._lam_option_e_setup_fail_logged = True
                            print(
                                f"{_PRINT_PREFIX} OPTION_E setup_offscreen_stage FAIL "
                                f"prim={inst.prim_path} resolved={resolved2}",
                                flush=True,
                            )
            # 2026-05-13: 자동 freeze / OmniGraph deactivate 는 비활성 — 두 함수 모두 no-op.
            # 호출만 남겨 두어 미래에 정책이 바뀌었을 때 한 군데(함수 본문)만 손대면 되도록 한다.
            self._ensure_option_e_freeze(inst, stage)
            self._ensure_option_e_omnigraph_deactivated(inst.prim_path, stage)

        # 2) playing instance 의 virtual_time 진행. 모든 instance (state 무관) 에 대해
        #    evaluate_and_write 를 호출하여 stopped/paused 도 마지막 vt 의 결과를 유지.
        for inst in self._registry.all_instances():
            if inst.state == "playing":
                self._advance_virtual_time(inst, dt)
            rt = self._runtime_by_path.get(inst.prim_path)
            if rt is None:
                continue
            if inst.prim_path in self._master_timeline_prims:
                # USD_TIMELINE (TBS) — omni.timeline 이 master stage 시간을 진행.
                # Option E offscreen 평가는 하지 않는다 (reference + OmniGraph 가 전역 시각으로 평가).
                continue
            if inst.prim_path not in self._evaluator_active_prims:
                # 2026-05-13 — TIMESAMPLES_REPLAY 가 한 번이라도 시작된 인스턴스만
                # default 를 author. 평상시(USD 로드 직후 / Reset 직후) 에는 reference
                # 의 timeSamples 가 master 타임라인을 따라 자유 재생되도록 evaluator 가
                # 손을 떼는 게 정책. ``begin_replay_mode`` 가 set 에 추가하고
                # ``end_replay_mode`` (Reset 시) 가 제거하며, 제거 시에는 inst sublayer
                # 에 박혀 있던 default opinion 도 함께 청소된다.
                continue
            if not rt.is_ready:
                # 첫 frame 한 번만 진단 출력 — setup 이 실패한 이유를 추적.
                if not getattr(rt, "_diag_not_ready_logged", False):
                    try:
                        rt._diag_not_ready_logged = True  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    print(
                        f"{_PRINT_PREFIX} OPTION_E runtime NOT_READY prim={inst.prim_path} "
                        f"offscreen={bool(rt.offscreen_asset_path)} master={'set' if stage else 'None'}",
                        flush=True,
                    )
                continue
            # 첫 호출 시점 명시 — H1/H2 가설 분기를 즉시 식별.
            if not getattr(rt, "_diag_first_call_logged", False):
                try:
                    rt._diag_first_call_logged = True  # type: ignore[attr-defined]
                except Exception:
                    pass
                print(
                    f"{_PRINT_PREFIX} OPTION_E first evaluate prim={inst.prim_path} "
                    f"state={inst.state} vt={inst.virtual_time:.3f}s "
                    f"offscreen_asset={rt.offscreen_asset_path!r}",
                    flush=True,
                )
            # ----------------------------------------------------------------
            # Option E core fix (2026-05-12) — reauthor 를 stronger sublayer 로.
            #
            # `evaluate_and_write` 가 `mirror_attr.Set(val)` 로 default 를 박는데,
            # 매 frame _on_update 첫 부분에서 EditTarget 이 root layer 로 강제되어
            # default 가 root layer 에 박힌다. 그러나 LAM session layer 의 strongest
            # 슬롯에 끼운 `lam_inst_<tag>` sublayer 안에 freeze 용 explicit reference
            # (`LayerOffset(0, 1e-9)`) 가 박혀 있어 — USD value resolution 상 sublayer
            # 가 root layer 보다 stronger 라 reference 가 가져오는 timeSamples 가
            # winner 가 되고 root 의 default 는 마스킹된다 (=viewport 변화 없음).
            #
            # 해결: 매 frame `evaluate_and_write` 직전에 master stage 의 EditTarget 을
            # 해당 인스턴스의 sublayer 로 잠깐 옮긴다. 그러면 default 가 sublayer 의
            # prim spec 위에 박히고, 같은 sublayer 안에서 reference 는 weaker
            # composition arc (= referenced layer 의 opinion) 이라 default 가 winner
            # 가 된다. omni.timeline 진행 / master stage 시각 진행 없이 인스턴스마다
            # 자기 virtual_time 으로 평가된 값이 독립적으로 viewport 에 반영된다 —
            # 즉 멀티 USD / 멀티 인스턴스 timeSamples replay 의 핵심 메커니즘.
            # ----------------------------------------------------------------
            inst_sublayer = None
            try:
                if self._master is not None:
                    inst_sublayer = self._master.get_inst_sublayer(inst.prim_path)
                    if inst_sublayer is None:
                        try:
                            inst_sublayer = self._master.ensure_inst_sublayer(
                                inst.prim_path,
                                tag_hint=inst.instance_id or inst.prim_path,
                            )
                        except Exception:
                            inst_sublayer = None
            except Exception:
                inst_sublayer = None

            edit_target_switched = False
            if (
                inst_sublayer is not None
                and stage is not None
                and _Usd is not None
            ):
                try:
                    stage.SetEditTarget(_Usd.EditTarget(inst_sublayer))
                    edit_target_switched = True
                except Exception as exc:
                    if not getattr(rt, "_diag_edit_target_warned", False):
                        try:
                            rt._diag_edit_target_warned = True  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        print(
                            f"{_PRINT_PREFIX} OPTION_E EditTarget switch FAIL "
                            f"prim={inst.prim_path} sublayer="
                            f"{getattr(inst_sublayer, 'identifier', '?')!r}: {exc}",
                            flush=True,
                        )

            try:
                rt.evaluate_and_write(
                    inst.virtual_time,
                    snap_timecode_to_frame=self._snap_timecode_to_frame,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} runtime.evaluate_and_write FAIL "
                    f"prim={inst.prim_path}: {exc}",
                    flush=True,
                )
            finally:
                if edit_target_switched:
                    try:
                        self._master.set_root_layer_edit_target()
                    except Exception:
                        pass

    def _advance_virtual_time(self, inst: AnimationInstance, dt: float) -> None:
        """Option E 경로용 — 인스턴스의 `virtual_time` 만 1 frame 진행한다.

        loop wrap / completed 시 state 전환은 기존 `_tick_instance` 와 동일하지만,
        본 method 는 reauthor 캐시 / LayerOffset mapping / stage time set 등 핫픽스
        6-10 의 부수 효과를 **일절 수행하지 않는다** (Option E 가 그 경로를 대체하므로).
        """
        prev_t = inst.virtual_time
        sp = max(0.01, float(inst.speed)) * max(0.01, float(self._global_speed))
        new_t = prev_t + dt * sp

        end_t = self._end_time_seconds(inst)
        start_t = self._start_time_seconds(inst)

        completed = False
        if end_t > start_t and new_t >= end_t:
            if inst.loop:
                length = end_t - start_t
                if length > 1e-6:
                    new_t = start_t + ((new_t - start_t) % length)
                else:
                    new_t = start_t
            else:
                new_t = end_t
                completed = True

        inst.virtual_time = new_t
        if completed:
            inst.state = "stopped"
            print(
                f"{_PRINT_PREFIX} completed prim={inst.prim_path} (option_e) "
                f"prev_t={prev_t:.3f}s new_t={new_t:.3f}s dt={dt:.4f}s sp={sp:.3f} "
                f"start_t={start_t:.3f}s end_t={end_t:.3f}s",
                flush=True,
            )

    def _ensure_option_e_freeze(self, inst: AnimationInstance, stage) -> None:
        """no-op (2026-05-12 사용자 요청) — master 타임라인 자유 재생을 위해 비활성화.

        과거에는 LAM session sublayer 에 `LayerOffset(0, 1e-9)` 를 explicit override 로
        author 해 reference timeSamples 평가를 micro-freeze 시키고, TIMESAMPLES_REPLAY
        모드에서 매 frame `evaluate_and_write` 가 author 한 default 가 winner 가 되도록
        했다. 그 결과:

        - USD 추가 직후 master 타임라인을 돌려도 prim 이 움직이지 않음.
        - USD_TIMELINE step 이 끝나면 다시 freeze 가 author 되어 timeline 이 다시 막힘.

        사용자가 "타임라인은 테스트용으로 쓰기 때문에 막지 않아도 된다 — 막는 코드를
        모두 제거" 를 요청 → 본 함수는 의도적으로 비워둔다. multi-instance 독립 재생을
        다시 살릴 때 본 함수의 본문(과 `_ensure_option_e_omnigraph_deactivated`)을 복구
        하면 된다.
        """
        return

    # ------------------------------------------------------------- timeline drive

    def _get_timeline(self):
        """omni.timeline 인터페이스를 lazily 캐싱한다."""
        if self._timeline_iface is not None:
            return self._timeline_iface
        if _ot is None:
            return None
        try:
            self._timeline_iface = _ot.get_timeline_interface()
            return self._timeline_iface
        except Exception as exc:
            if not self._timeline_warned_once:
                self._timeline_warned_once = True
                print(f"{_PRINT_PREFIX} omni.timeline interface acquire failed: {exc}", flush=True)
            return None

    def _advance_stage_time(self, stage, eval_seconds: float, *, owner_prim: Optional[str] = None) -> None:
        """Master stage 의 current time 을 evaluator 의 가상 시각으로 set 한다.

        - omni.timeline.set_current_time(seconds) 는 main thread 에서 호출되어야 한다(여기서 호출).
        - master stage 의 endTimeCode 가 충분치 않으면 자동 확장한다(없으면 set 가 clamp 될 수 있음).
        """
        ti = self._get_timeline()
        if ti is None:
            return

        # stage end 확장 — set 하려는 timecode 가 현재 endTimeCode 를 넘으면 늘려준다.
        try:
            # FPS 30 고정 정책 — 자산 / master 의 tps 와 무관하게 30 사용.
            tps_val = LAM_FIXED_FPS
            timecode = float(eval_seconds) * float(tps_val)
            if stage is not None:
                try:
                    cur_end = float(stage.GetEndTimeCode())
                    if timecode > cur_end:
                        new_end = max(timecode * 1.5, cur_end + 1.0)
                        try:
                            stage.SetEndTimeCode(new_end)
                        except Exception:
                            pass
                        if not self._stage_end_warned_once:
                            self._stage_end_warned_once = True
                            print(
                                f"{_PRINT_PREFIX} stage endTimeCode auto-expanded "
                                f"{cur_end:.3f} → {new_end:.3f} (owner={owner_prim})",
                                flush=True,
                            )
                except Exception:
                    pass
        except Exception:
            pass

        try:
            ti.set_current_time(float(eval_seconds))
        except Exception as exc:
            if not self._timeline_warned_once:
                self._timeline_warned_once = True
                print(f"{_PRINT_PREFIX} timeline.set_current_time failed: {exc}", flush=True)

    # --------------------------------------------------------- per-instance mapping

    # Hotfix6.2 — 사용자 요구로 LAM 의 시간 변환은 30fps 로 고정한다.
    # (master stage 의 timeCodesPerSecond 는 변경하지 않아 TBS 영향 0.
    #  evaluator 의 시각 변환만 30 으로 강제 — inst.asset_tps 도 30 외 값이면 30 으로 정규화.)
    LAM_FIXED_FPS: float = 30.0

    def _stage_tps(self, stage) -> float:  # noqa: ARG002
        return self.LAM_FIXED_FPS

    def _ensure_stage_fps_lam_fixed(self, stage) -> None:
        """master stage 의 timeCodesPerSecond / framesPerSecond 를 LAM_FIXED_FPS 로 강제.

        한 번만 author. 사용자 master USD 가 60fps 등으로 author 된 경우 omni.timeline
        슬라이더가 그 값으로 표시되는 문제를 해결한다.
        """
        if stage is None or getattr(self, "_diag_stage_fps_done", False):
            return
        target = float(self.LAM_FIXED_FPS)
        try:
            cur_tcps = float(stage.GetTimeCodesPerSecond())
        except Exception:
            cur_tcps = -1.0
        try:
            cur_fps = float(stage.GetFramesPerSecond())
        except Exception:
            cur_fps = -1.0
        if abs(cur_tcps - target) > 1e-6:
            try:
                stage.SetTimeCodesPerSecond(target)
                print(
                    f"{_PRINT_PREFIX} stage timeCodesPerSecond {cur_tcps} → {target}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} SetTimeCodesPerSecond failed: {exc}",
                    flush=True,
                )
        if abs(cur_fps - target) > 1e-6:
            try:
                stage.SetFramesPerSecond(target)
                print(
                    f"{_PRINT_PREFIX} stage framesPerSecond {cur_fps} → {target}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} SetFramesPerSecond failed: {exc}",
                    flush=True,
                )
        self._diag_stage_fps_done = True

    def _sync_layer_offset_mapping(
        self, inst: AnimationInstance, stage, *, master_tc: float, tps_master: float
    ) -> None:
        """Hotfix6 — Per-Instance Layer Offset Mapping.

        매 frame 호출되지만 실제 author 는 (state, vt_at_resync, speed, loop_lap) 시그니처
        가 변경된 시점에만 일어난다.

        매핑:
          inst_tc(t) = layerOffset.offset + master_tc(t) * layerOffset.scale

        playing 진입 시 한 번만 author 하면, master_tc 가 wall clock 으로 진행되는 한
        inst_tc 가 자동으로 inst.virtual_time*tps + asset_start_tc 와 sync 된다.
        loop wrap / speed 변경 / paused 등은 시그니처가 바뀌어 다시 author.

        Hotfix6.1 — `_has_lam_reference` 가드 제거. 이 함수는 self._registry.all_instances()
        가 순회하는 인스턴스만 받기 때문에 LAM Registry 에 등록된 prim 만 도달한다.
        TBS 등 다른 자산은 LAM Registry 에 없어 호출 자체가 일어나지 않는다.
        과거 가드는 customData('lam:instance') 가 USD save/load 사이에서 형식이 달라지면
        False 를 반환해 mapping 이 적용되지 않는 문제가 있었음.
        """
        if _Sdf is None or stage is None:
            return

        cur_state = inst.state

        # 인스턴스마다 첫 호출 시 1 회 진단 — registry 등록 / prim 존재 / references 개수 확인.
        # + composition stack 진단(어떤 composition arc 로 합쳐졌는지 — reference / payload /
        #   sublayer 어느 것인지 파악).
        if not getattr(inst, "_lam_diag_mapping_seen", False):
            try:
                inst._lam_diag_mapping_seen = True  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                prim_dbg = stage.GetPrimAtPath(inst.prim_path)
                prim_valid = bool(prim_dbg and prim_dbg.IsValid())
                refs_count = 0
                if prim_valid:
                    try:
                        refs_count = len(list(prim_dbg.GetReferences().GetAddedOrExplicitItems()))
                    except Exception:
                        refs_count = -1
                print(
                    f"{_PRINT_PREFIX} mapping diag prim={inst.prim_path} state={cur_state} "
                    f"prim_valid={prim_valid} refs_count={refs_count} "
                    f"asset_start_tc={inst.asset_start_time} tps={inst.asset_tps}",
                    flush=True,
                )
                # composition stack 진단 — prim 이 어떤 layer 의 어떤 spec 들로 합쳐졌는지.
                # LAM 이 추가한 sublayer 는 layer identifier 에 'lam_inst_' 가 포함되어
                # 별도 표기로 구분한다.
                if prim_valid:
                    try:
                        stack = prim_dbg.GetPrimStack()
                        for i, spec in enumerate(stack):
                            try:
                                layer_id = spec.layer.identifier if spec.layer else "?"
                            except Exception:
                                layer_id = "?"
                            tag = "[LAM]" if "lam_inst_" in layer_id else ""
                            try:
                                refs = (
                                    list(spec.referenceList.GetAddedOrExplicitItems())
                                    if spec.referenceList
                                    else []
                                )
                            except Exception:
                                refs = []
                            try:
                                pays = (
                                    list(spec.payloadList.GetAddedOrExplicitItems())
                                    if spec.payloadList
                                    else []
                                )
                            except Exception:
                                pays = []
                            ref_paths = [str(r.assetPath) for r in refs]
                            pay_paths = [str(p.assetPath) for p in pays]
                            print(
                                f"{_PRINT_PREFIX}   stack[{i}]{tag} layer={layer_id} "
                                f"refs={ref_paths} payloads={pay_paths}",
                                flush=True,
                            )
                    except Exception as exc:
                        print(f"{_PRINT_PREFIX}   composition stack exc: {exc}", flush=True)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} mapping diag exc prim={inst.prim_path}: {exc}", flush=True)
        # Hotfix6.2 — fps 30 고정. inst.asset_tps / master stage tps 모두 무시하고 30 사용.
        # (사용자 자산이 모두 30fps 이며, lam 이 fps 추론 logic 으로 다른 값을 쓰지 않도록 강제.)
        # 또한 master stage 의 timeCodesPerSecond / framesPerSecond 도 30 으로 강제(omni.timeline
        # 슬라이더가 60fps 등 다른 값으로 표시되는 문제 해결). 1 회만 author.
        self._ensure_stage_fps_lam_fixed(stage)
        tps = self.LAM_FIXED_FPS
        asset_start_tc = float(inst.asset_start_time)
        speed = float(inst.speed) * float(self._global_speed)
        if speed <= 0.0:
            speed = 0.0  # paused 와 동등(아래 freeze 분기로 처리되지 않으므로 별도 케이스).

        if cur_state == "playing":
            # Hotfix6: playing 시그니처에 vt 를 넣지 않는다. playing 진입 시 한 번 author 하면
            # 이후 master_tc(wall clock) 가 진행해도 inst_tc 가 자동으로 inst.virtual_time*tps 와 sync.
            # speed / loop 변경 시 시그니처가 바뀌어 자동 재author. loop wrap / 외부 seek 은
            # _tick_instance 가 invalidate_mapping(prim_path) 으로 명시적 무효화.
            sig = ("PLAY", round(speed, 4), bool(inst.loop))
            if self._last_mapping_sig.get(inst.prim_path) == sig:
                return  # 시그니처 변경 없음 — author skip.

            # offset = (inst_tc_now) - (master_tc_now * scale)
            # inst_tc_now = inst.virtual_time*tps + asset_start_tc
            # 이렇게 author 하면 master_tc 가 진행해도 inst_tc 가 inst.vt 와 sync.
            inst_tc_now = float(inst.virtual_time) * tps + asset_start_tc
            scale_v = speed
            offset_v = inst_tc_now - master_tc * scale_v
            self._last_mapping_sig[inst.prim_path] = sig
            ok = self._set_prim_layer_offset(stage, inst.prim_path, offset=offset_v, scale=scale_v)
            if ok:
                print(
                    f"{_PRINT_PREFIX} mapping prim={inst.prim_path} PLAY "
                    f"offset={offset_v:.3f}tc scale={scale_v:.3f} "
                    f"(master_tc={master_tc:.3f} inst_tc_now={inst_tc_now:.3f} tps={tps})",
                    flush=True,
                )
            elif not self._freeze_warned_once:
                self._freeze_warned_once = True
                print(
                    f"{_PRINT_PREFIX} mapping FAILED prim={inst.prim_path} PLAY "
                    f"offset={offset_v:.3f} scale={scale_v:.3f} — references not found?",
                    flush=True,
                )
            return

        # stopped / paused → freeze. inst_tc 를 그 시점의 vt 로 박고 scale=0.
        freeze_inst_tc = float(inst.virtual_time) * tps + asset_start_tc
        sig = ("FREEZE", round(freeze_inst_tc, 3))
        if self._last_mapping_sig.get(inst.prim_path) == sig:
            return
        self._last_mapping_sig[inst.prim_path] = sig
        ok = self._set_prim_layer_offset(stage, inst.prim_path, offset=freeze_inst_tc, scale=0.0)
        if ok:
            print(
                f"{_PRINT_PREFIX} mapping prim={inst.prim_path} FREEZE "
                f"offset={freeze_inst_tc:.3f}tc scale=0.000 (state={cur_state})",
                flush=True,
            )
        elif not self._freeze_warned_once:
            self._freeze_warned_once = True
            print(
                f"{_PRINT_PREFIX} mapping FAILED prim={inst.prim_path} FREEZE "
                f"offset={freeze_inst_tc:.3f} — references not found?",
                flush=True,
            )

    def _has_lam_reference(self, stage, prim_path: str) -> bool:
        """[Deprecated, Hotfix6.1] customData('lam:instance') 가 USD save/load 사이에서
        형식이 달라지면 False 를 반환해 LayerOffset mapping 이 적용되지 않는 문제가 있어
        호출 경로에서 제거됨. registry.all_instances() 가 순회하는 인스턴스만 도달하므로
        guard 자체가 over-protection 이었음. 호환 보존을 위해 함수만 남겨둠.
        """
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                return False
            try:
                v = prim.GetCustomDataByKey("lam:instance")
                if v is True or v == 1 or v == "true":
                    return True
            except Exception:
                pass
            return False
        except Exception:
            return False

    # ---------------- (deprecated) 핫픽스 5 의 단순 freeze — 호환 보존을 위해 코드만 남김.

    def _sync_freeze_state(self, inst: AnimationInstance, stage) -> None:
        """[Deprecated] Hotfix5 의 단순 freeze. Hotfix6 에서는 더 이상 호출하지 않는다.

        - playing → SdfLayerOffset(0, 1) : 정상 평가 (stage current time 따라 진행)
        - stopped/paused → SdfLayerOffset(freeze_tc, 0) : 시각 평가 freeze (frozen frame)
        """
        cur_state = inst.state
        last = self._last_state_seen.get(inst.prim_path)
        if last == cur_state:
            return
        self._last_state_seen[inst.prim_path] = cur_state

        tps = LAM_FIXED_FPS
        # freeze 시각: 인스턴스의 마지막 평가된 시각(virtual_time + offset_sec) 를 timeCode 로 변환.
        # 첫 등록 직후엔 virtual_time=0 → freeze_tc=asset_start_tc 로 첫 frame 에 머무름.
        freeze_tc = (float(inst.virtual_time) + float(inst.offset_sec)) * float(tps)
        if freeze_tc <= 0.0:
            freeze_tc = float(inst.asset_start_time)

        if cur_state == "playing":
            offset, scale = 0.0, 1.0
            mode = "PLAY"
        else:
            offset, scale = freeze_tc, 0.0
            mode = "FREEZE"

        ok = self._set_prim_layer_offset(stage, inst.prim_path, offset=offset, scale=scale)
        if not ok and not self._freeze_warned_once:
            self._freeze_warned_once = True
            print(
                f"{_PRINT_PREFIX} freeze sync FAILED prim={inst.prim_path} "
                f"({mode} offset={offset:.3f} scale={scale:.3f}) — references not found?",
                flush=True,
            )
            return
        if ok:
            print(
                f"{_PRINT_PREFIX} freeze sync prim={inst.prim_path} {mode} "
                f"offset={offset:.3f} scale={scale:.3f} (state {last} → {cur_state})",
                flush=True,
            )

    # ---------------- Hotfix7 — Per-instance Sublayer Override (Opt-1) ----------------

    def _walk_prim_stack_first_ref_or_payload(
        self, stage, usd_prim_path: str
    ):
        """주어진 prim path 의 PrimStack 에서 lam_inst_* 가 아닌 레이어의 ref/payload 1개."""
        if _Sdf is None or stage is None:
            return None
        try:
            prim = stage.GetPrimAtPath(usd_prim_path)
            if not prim or not prim.IsValid():
                return None
            for spec in prim.GetPrimStack():
                try:
                    layer_id = spec.layer.identifier if spec.layer else ""
                except Exception:
                    layer_id = ""
                if layer_id and ("lam_inst_" in layer_id):
                    continue
                try:
                    refs = list(spec.referenceList.GetAddedOrExplicitItems()) if spec.referenceList else []
                except Exception:
                    refs = []
                for r in refs:
                    if not r or not getattr(r, "assetPath", None):
                        continue
                    try:
                        cd = dict(r.customData) if getattr(r, "customData", None) else {}
                    except Exception:
                        cd = {}
                    return ("ref", r.assetPath, r.primPath, cd)
                try:
                    pays = list(spec.payloadList.GetAddedOrExplicitItems()) if spec.payloadList else []
                except Exception:
                    pays = []
                for p in pays:
                    if not p or not getattr(p, "assetPath", None):
                        continue
                    return ("pay", p.assetPath, p.primPath, {})
        except Exception:
            return None
        return None

    def _extract_source_ref_template(self, stage, prim_path: str):
        """master PrimStack 에서 ref/payload 템플릿 + **sublayer 에 author 할 prim 경로** 추출.

        반환: ``(kind, assetPath, primPathInAsset, customData, author_prim_path)`` 5-튜플.

        - ``author_prim_path`` 는 대부분 ``prim_path``(인스턴스) 이다.
        - **Empty + drag&drop** 처럼 자산 ref 가 인스턴스 직속이 아니라 ``/World/aaa/test1``
          등 자식에만 있으면, registry ``source_asset`` 으로 인스턴스 루트에 전체 자산을
          또 reference 하면 자산이 **이중 합성**된다. 이 경우 drag 앵커 prim 에서 ref 를
          읽어 ``author_prim_path = 앵커`` 로 반환한다.
        - 인스턴스 prim 에 직접 ref 가 있으면 그걸 우선 사용한다.
        """
        if _Sdf is None or stage is None:
            return None
        inst_path = (prim_path or "").rstrip("/")
        if not inst_path.startswith("/"):
            return None

        hit = self._walk_prim_stack_first_ref_or_payload(stage, inst_path)
        if hit is not None:
            return (*hit, inst_path)

        # 인스턴스 직속 ref 없음 — drag&drop 앵커에서 ref 찾기 (registry 루트 fallback 전).
        try:
            from .lam_extract_from_master import (
                _discover_asset_path_from_master,
                discover_drag_drop_asset_root_prim,
                normalize_asset_uri_to_path,
            )

            inst = self._registry.get_by_prim_path(prim_path)
            sp = ""
            if inst is not None:
                sp = normalize_asset_uri_to_path(
                    (getattr(inst, "source_asset", "") or "").strip()
                )
            if not sp:
                sp = normalize_asset_uri_to_path(
                    _discover_asset_path_from_master(stage, inst_path) or ""
                )
            anchor = ""
            if sp:
                anchor = (
                    discover_drag_drop_asset_root_prim(stage, inst_path, sp) or ""
                ).rstrip("/")
            if anchor and anchor != inst_path:
                hit2 = self._walk_prim_stack_first_ref_or_payload(stage, anchor)
                if hit2 is not None:
                    print(
                        f"{_PRINT_PREFIX} ref-template from drag anchor prim={anchor} "
                        f"(inst={inst_path}, avoids duplicate root ref)",
                        flush=True,
                    )
                    return (*hit2, anchor)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} ref-template drag-anchor probe failed prim={inst_path}: {exc}",
                flush=True,
            )

        # 레거시: [USD 추가] 로 인스턴스 prim 자체에 reference 가 박힌 흐름.
        try:
            inst = self._registry.get_by_prim_path(prim_path)
            if inst is not None and getattr(inst, "source_asset", None):
                return (
                    "ref",
                    str(inst.source_asset),
                    _Sdf.Path.emptyPath,
                    {},
                    inst_path,
                )
        except Exception:
            pass
        return None

    # 핫픽스 10 — USD `SdfLayerOffset(scale=0)` 은 invalid LayerOffset 으로 평가 시
    # unspecified behavior(`IsValid()==False`). 일부 USD 빌드에서는 자동으로 identity
    # `(0, 1)` 로 fallback 되어 freeze 가 시각적으로 무시되는 사례가 보고됨.
    # 따라서 freeze 시 scale=0 이 들어오면 매우 작은 양수(1e-9) 로 자동 보정한다.
    # - master_tc 가 1e9 sec 진행해도 inst_tc 는 ~1 sec 만 진행 → 시각적으로 freeze 동등.
    # - IsValid()=True 라 USD 평가에서 정상 처리됨.
    LAM_FREEZE_MIN_SCALE: float = 1.0e-9

    def _set_prim_layer_offset(self, stage, prim_path: str, *, offset: float, scale: float) -> bool:
        """Hotfix7 (Opt-1) — 인스턴스 전용 sublayer 안에 explicit reference 1개를 author.

        과거 (Hotfix6.x) 의 root layer/master USD 직접 조작 방식은 폐기.
        - 사용자 master USD 는 절대 변경되지 않음(save 영향 0).
        - sublayer 가 root layer 보다 stronger 슬롯(subLayerPaths.insert(0, ...)) 에 있어
          USD ListOp explicit override 가 무조건 winner.
        - `Sdf.ChangeBlock` 으로 묶어 한 번의 ChangeNotice → Hydra cache 안전 invalidate.
        - Hotfix10 — scale 이 0 에 가까우면 LAM_FREEZE_MIN_SCALE 로 자동 보정(invalid LayerOffset 회피).
        """
        if _Sdf is None or stage is None or self._master is None:
            return False
        # 핫픽스 10 — scale 자동 보정. abs(scale) < 1e-12 면 invalid 로 간주하여 1e-9 로 변환.
        scale_in = float(scale)
        if abs(scale_in) < 1e-12:
            scale = self.LAM_FREEZE_MIN_SCALE
            if not getattr(self, "_diag_freeze_scale_clamp_warned", False):
                self._diag_freeze_scale_clamp_warned = True
                print(
                    f"{_PRINT_PREFIX} freeze scale clamped: scale={scale_in:.3g} -> "
                    f"{self.LAM_FREEZE_MIN_SCALE:.1e} (USD invalid LayerOffset 회피)",
                    flush=True,
                )

        # 1) source ref template 1개 추출(원본 reference 의 assetPath/primPath 보존).
        tmpl = getattr(self, "_src_ref_tmpl_cache", None)
        if tmpl is None:
            tmpl = {}
            self._src_ref_tmpl_cache = tmpl
        src = tmpl.get(prim_path)
        if src is None:
            src = self._extract_source_ref_template(stage, prim_path)
            if src is None:
                if not getattr(self, "_diag_no_src_warned", False):
                    self._diag_no_src_warned = True
                    print(
                        f"{_PRINT_PREFIX} _set_prim_layer_offset no source ref/payload found "
                        f"prim={prim_path} (registry source_asset 도 없음)",
                        flush=True,
                    )
                return False
            tmpl[prim_path] = src
        if len(src) == 5:
            kind, asset_path, prim_path_in_asset, custom_data, author_prim_path = src
        else:
            kind, asset_path, prim_path_in_asset, custom_data = src  # type: ignore[misc]
            author_prim_path = prim_path
        author_prim_path = (author_prim_path or prim_path).strip()
        if not author_prim_path.startswith("/"):
            author_prim_path = prim_path

        # 2) 인스턴스 전용 sublayer 확보(없으면 생성하여 root layer 의 strongest 슬롯에 삽입).
        inst = self._registry.get_by_prim_path(prim_path)
        tag_hint = inst.instance_id if (inst is not None and inst.instance_id) else prim_path
        sublayer = self._master.ensure_inst_sublayer(prim_path, tag_hint=tag_hint)
        if sublayer is None:
            if not getattr(self, "_diag_no_sublayer_warned", False):
                self._diag_no_sublayer_warned = True
                print(
                    f"{_PRINT_PREFIX} _set_prim_layer_offset cannot ensure sublayer "
                    f"prim={prim_path}",
                    flush=True,
                )
            return False

        # 3) sublayer 안에 prim spec 만들고 reference/payload 1개 explicit set.
        try:
            with _Sdf.ChangeBlock():
                sub_spec = sublayer.GetPrimAtPath(author_prim_path)
                if sub_spec is None:
                    sub_spec = _Sdf.CreatePrimInLayer(sublayer, _Sdf.Path(author_prim_path))
                if sub_spec is None:
                    return False
                # 핫픽스 9 — Specifier.Def 로 강한 prim spec 으로 만든다.
                # SpecifierOver 는 weaker layer 의 prim spec 정의에 over-author 만 하는 의미라
                # weaker reference compose 가 함께 살아남는 케이스가 보고됨.
                # Specifier.Def 는 새로운 prim 정의로 처리되어 USD ListOp explicit override
                # 의 strongest 결정성이 향상된다.
                try:
                    sub_spec.specifier = _Sdf.SpecifierDef
                except Exception:
                    pass

                # 핫픽스 9 — explicit 모드 강제. ListOp merge semantics 를 완전히 회피하기 위해
                # ClearEdits/SetItems/fallback chain 을 모두 제거하고 explicitItems 직접 대입만 사용.
                if kind == "ref":
                    new_ref = _Sdf.Reference(
                        assetPath=asset_path,
                        primPath=prim_path_in_asset if prim_path_in_asset else _Sdf.Path.emptyPath,
                        layerOffset=_Sdf.LayerOffset(float(offset), float(scale)),
                        customData=custom_data,
                    )
                    try:
                        sub_spec.referenceList.explicitItems = [new_ref]  # type: ignore[assignment]
                    except Exception as exc:
                        print(
                            f"{_PRINT_PREFIX} _set_prim_layer_offset explicitItems set FAILED "
                            f"(ref) prim={prim_path}: {exc}",
                            flush=True,
                        )
                        return False
                else:
                    new_pay = _Sdf.Payload(
                        assetPath=asset_path,
                        primPath=prim_path_in_asset if prim_path_in_asset else _Sdf.Path.emptyPath,
                        layerOffset=_Sdf.LayerOffset(float(offset), float(scale)),
                    )
                    try:
                        sub_spec.payloadList.explicitItems = [new_pay]  # type: ignore[assignment]
                    except Exception as exc:
                        print(
                            f"{_PRINT_PREFIX} _set_prim_layer_offset explicitItems set FAILED "
                            f"(pay) prim={prim_path}: {exc}",
                            flush=True,
                        )
                        return False

            # 핫픽스 9 (수정 3) — Hydra/usdImaging 의 prototype sharing 방지.
            # 같은 (assetPath, primPath) 를 가진 reference 들이 Hydra prototype 으로 공유되어
            # 같은 timeline evaluation 을 share 하면 LayerOffset 이 무력화된다.
            # SetInstanceable(False) 로 instancing 을 명시적으로 disable.
            try:
                p_now = stage.GetPrimAtPath(author_prim_path)
                if p_now and p_now.IsValid():
                    try:
                        if p_now.IsInstanceable():
                            p_now.SetInstanceable(False)
                    except Exception:
                        # prim 이 instanceable 가 아니라도 명시적으로 False 박는다.
                        try:
                            p_now.SetInstanceable(False)
                        except Exception:
                            pass
            except Exception:
                pass

            # 진단(인스턴스마다 첫 author 시 1회) — 어느 layer 에 author 했고 어떤 asset 인지.
            seen = getattr(self, "_diag_sublayer_authored_seen", None)
            if seen is None:
                seen = set()
                self._diag_sublayer_authored_seen = seen
            if prim_path not in seen:
                seen.add(prim_path)
                try:
                    sub_id = sublayer.identifier
                except Exception:
                    sub_id = "?"

                # ----- composed metadata 진단(수정 4) -----
                # 실제 compose 결과의 references/payload metadata 자체를 출력해
                # explicitItems=[only LAM ref] 인지, weaker refs 와 merge 됐는지 확인.
                composed_refs_meta = None
                composed_pays_meta = None
                composed_refs = -1
                composed_pays = -1
                try:
                    p = stage.GetPrimAtPath(prim_path)
                    if p and p.IsValid():

                        def _count_listop(meta):
                            if meta is None:
                                return 0
                            n = 0
                            for attr in (
                                "explicitItems",
                                "prependedItems",
                                "appendedItems",
                                "orderedItems",
                                "addedItems",
                            ):
                                try:
                                    items = getattr(meta, attr, None)
                                    if items is not None:
                                        n += len(list(items))
                                except Exception:
                                    pass
                            return n

                        try:
                            composed_refs_meta = p.GetMetadata("references")
                            composed_refs = _count_listop(composed_refs_meta)
                        except Exception:
                            composed_refs = -2
                        try:
                            composed_pays_meta = p.GetMetadata("payload")
                            composed_pays = _count_listop(composed_pays_meta)
                        except Exception:
                            composed_pays = -2
                except Exception:
                    pass
                print(
                    f"{_PRINT_PREFIX} sublayer mapping authored inst={prim_path} "
                    f"sublayer_spec={author_prim_path} layer={sub_id} kind={kind} "
                    f"asset={asset_path} src_prim={prim_path_in_asset} "
                    f"offset={offset:.3f} scale={scale:.3f} "
                    f"composed_refs={composed_refs} composed_pays={composed_pays}",
                    flush=True,
                )
                print(
                    f"{_PRINT_PREFIX} composed metadata prim={prim_path} "
                    f"references={composed_refs_meta} payloads={composed_pays_meta}",
                    flush=True,
                )

                # ----- post-attach stack 진단(수정 5) -----
                # asset / primPath / LayerOffset(offset, scale) 까지 함께 출력.
                try:
                    p = stage.GetPrimAtPath(prim_path)
                    if p and p.IsValid():
                        prim_stack_after = list(p.GetPrimStack())
                        for j, spec_after in enumerate(prim_stack_after):
                            try:
                                lid = spec_after.layer.identifier if spec_after.layer else "?"
                            except Exception:
                                lid = "?"
                            tag2 = "[LAM]" if "lam_inst_" in lid else ""

                            def _gather_arc_items(arc_list):
                                items_out = []
                                if arc_list is None:
                                    return items_out
                                for src in (
                                    "explicitItems",
                                    "prependedItems",
                                    "appendedItems",
                                    "orderedItems",
                                    "addedItems",
                                ):
                                    try:
                                        for it in getattr(arc_list, src):
                                            items_out.append((src, it))
                                    except Exception:
                                        pass
                                return items_out

                            refs2 = _gather_arc_items(spec_after.referenceList)
                            pays2 = _gather_arc_items(spec_after.payloadList)
                            print(
                                f"{_PRINT_PREFIX}   post-attach stack[{j}]{tag2} layer={lid} "
                                f"refs_count={len(refs2)} pays_count={len(pays2)}",
                                flush=True,
                            )
                            for src, r in refs2:
                                try:
                                    lo = r.layerOffset
                                    lo_o = float(lo.offset)
                                    lo_s = float(lo.scale)
                                except Exception:
                                    lo_o = float("nan")
                                    lo_s = float("nan")
                                print(
                                    f"{_PRINT_PREFIX}     ref[{src}] asset={r.assetPath} "
                                    f"prim={r.primPath} offset={lo_o:.3f} scale={lo_s:.3f}",
                                    flush=True,
                                )
                            for src, py in pays2:
                                try:
                                    lo = py.layerOffset
                                    lo_o = float(lo.offset)
                                    lo_s = float(lo.scale)
                                except Exception:
                                    lo_o = float("nan")
                                    lo_s = float("nan")
                                print(
                                    f"{_PRINT_PREFIX}     pay[{src}] asset={py.assetPath} "
                                    f"prim={py.primPath} offset={lo_o:.3f} scale={lo_s:.3f}",
                                    flush=True,
                                )

                        # ----- TOP WINNER LAYER 진단(핫픽스 10 개선) -----
                        # 단순히 stack[0] 만 보면 안 됨: stack[0] 이 session anonymous spec 인 경우
                        # 그 spec 의 references 는 비어있고 실제 references winner 는 LAM sublayer.
                        # 따라서 "LAM sublayer 의 spec index 가 master USD spec index 보다 위에
                        # 있는지" 로 판정한다.
                        if prim_stack_after:
                            try:
                                top_layer = (
                                    prim_stack_after[0].layer.identifier
                                    if prim_stack_after[0].layer
                                    else "?"
                                )
                            except Exception:
                                top_layer = "?"

                            lam_idx = -1
                            master_idx = -1
                            for i, sp in enumerate(prim_stack_after):
                                try:
                                    lid = sp.layer.identifier if sp.layer else ""
                                except Exception:
                                    lid = ""
                                if lam_idx < 0 and "lam_inst_" in lid:
                                    lam_idx = i
                                # master USD spec 후보 — anonymous 가 아닌(file-backed)
                                # layer 이고 LAM 표지가 없는 첫 spec.
                                if master_idx < 0 and lid and (
                                    not lid.startswith("anon:") and "lam_inst_" not in lid
                                ):
                                    master_idx = i

                            # references winner 판정:
                            # - LAM 이 stack 에 있고
                            # - master USD spec 이 없거나 LAM 이 그보다 위(낮은 index = stronger)
                            is_lam_refs_winner = lam_idx >= 0 and (
                                master_idx < 0 or lam_idx < master_idx
                            )
                            print(
                                f"{_PRINT_PREFIX} TOP WINNER LAYER prim={prim_path} "
                                f"top={top_layer} lam_idx={lam_idx} master_idx={master_idx} "
                                f"is_lam_refs_winner={is_lam_refs_winner}",
                                flush=True,
                            )
                            if not is_lam_refs_winner:
                                print(
                                    f"{_PRINT_PREFIX} WARN LAM sublayer is NOT references winner "
                                    f"for prim={prim_path} — LayerOffset 이 무시될 수 있음",
                                    flush=True,
                                )
                        # instanceable 상태 확인 (수정 3 검증)
                        try:
                            print(
                                f"{_PRINT_PREFIX} prim instanceable={p.IsInstanceable()} "
                                f"is_instance={p.IsInstance()} prim={prim_path}",
                                flush=True,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
            return True
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} _set_prim_layer_offset error prim={prim_path} "
                f"offset={offset} scale={scale}: {exc}",
                flush=True,
            )
            return False

    # ----------------------------------------------------------------- helpers

    def _start_time_seconds(self, inst: AnimationInstance) -> float:
        mode, s, _ = inst.range
        tps = LAM_FIXED_FPS
        if mode == "frames":
            return float(s) / tps
        if mode == "ratio":
            length = max(0.0, inst.asset_end_time - inst.asset_start_time) / tps
            return (inst.asset_start_time / tps) + max(0.0, min(1.0, float(s))) * length
        return float(inst.asset_start_time) / tps

    def _end_time_seconds(self, inst: AnimationInstance) -> float:
        mode, s, e = inst.range
        tps = LAM_FIXED_FPS
        if mode == "frames":
            return float(e) / tps if e > s else self._start_time_seconds(inst)
        if mode == "ratio":
            length = max(0.0, inst.asset_end_time - inst.asset_start_time) / tps
            return (inst.asset_start_time / tps) + max(0.0, min(1.0, float(e))) * length
        return float(inst.asset_end_time) / tps


__all__ = ["RuntimeEvaluator"]

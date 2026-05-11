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
from typing import Dict, Optional

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
                    stage, inst, eval_seconds=new_t + float(inst.offset_sec)
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
                    if not ok_open:
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
                    rt.setup_offscreen_stage(self._resolve_instance_asset_path(inst))
            # Phase B-2-a — freeze sublayer 보장 (1회만 author).
            self._ensure_option_e_freeze(inst, stage)

        # 2) playing instance 의 virtual_time 진행. 모든 instance (state 무관) 에 대해
        #    evaluate_and_write 를 호출하여 stopped/paused 도 마지막 vt 의 결과를 유지.
        for inst in self._registry.all_instances():
            if inst.state == "playing":
                self._advance_virtual_time(inst, dt)
            rt = self._runtime_by_path.get(inst.prim_path)
            if rt is None:
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
            try:
                rt.evaluate_and_write(inst.virtual_time)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} runtime.evaluate_and_write FAIL "
                    f"prim={inst.prim_path}: {exc}",
                    flush=True,
                )

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
        """Phase B-2-a — master 의 자산 reference 의 timeline 평가만 freeze.

        `lam_multi_usd_loader.add_usd` 가 root layer 에 author 한 `addRef(asset)` 는
        그대로 두고, 본 함수가 **LAM session sublayer 안에서 explicit override**
        (`LayerOffset(0, 1e-9)`, instanceable=False) 를 1 회 author 한다.

        결과:
        - master 의 mesh / material / SkelRoot / SkelAnimation prim tree 는 reference
          로 그대로 보임 (사용자가 Stage 패널에서 prim path 복사 가능 — workflow 보존).
        - reference 의 timeSamples 평가는 scale=1e-9 로 사실상 정지 (USD invalid
          LayerOffset 회피용 micro scale).
        - runtime 의 매 frame `evaluate_and_write` 가 author 한 attribute default 가
          master root layer 의 stronger opinion → reference timeSamples 를 마스킹.
        - 사용자 master USD 파일은 root layer 의 평범한 reference 만 박혀있어서 변경 0
          (session sublayer 는 in-memory anonymous 라 자동 폐기).

        1 회만 호출되도록 `_option_e_freeze_seen` 으로 추적. instance unregister 시
        `forget_instance` 가 set 에서 제거.

        flag=False 경로에서는 `_on_update_option_e` 자체가 호출되지 않으므로 본 함수도
        호출되지 않음 → 회귀 0.
        """
        if _Sdf is None or stage is None or self._master is None:
            return
        if inst.prim_path in self._option_e_freeze_seen:
            return

        # 기존 핫픽스 9 의 _set_prim_layer_offset 가 정확히 동일한 author 절차
        # (inst sublayer 보장 + explicit override + instanceable=False + ChangeBlock) 를
        # 수행하므로 재사용. 단 본 호출은 1 회뿐이며 _last_mapping_sig 갱신 부수효과는
        # flag=True 경로에서 `_sync_layer_offset_mapping` 이 호출되지 않으므로 무해.
        ok = self._set_prim_layer_offset(
            stage,
            inst.prim_path,
            offset=0.0,
            scale=self.LAM_FREEZE_MIN_SCALE,
        )
        if ok:
            self._option_e_freeze_seen.add(inst.prim_path)
            print(
                f"{_PRINT_PREFIX} OPTION_E freeze authored prim={inst.prim_path} "
                f"offset=0 scale={self.LAM_FREEZE_MIN_SCALE:.1e}",
                flush=True,
            )
        else:
            # 자산 reference 가 root layer 에 아직 author 되지 않은 케이스 (예: 빈
            # registry-only 인스턴스) — 다음 frame 에서 다시 시도 가능. (`_set_prim_layer_offset`
            # 가 _diag_no_src_warned 로 1 회만 경고 출력.)
            pass

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

    def _extract_source_ref_template(self, stage, prim_path: str):
        """master USD 의 PrimStack 에서 첫 번째 reference/payload 1개를 추출해
        (assetPath, primPath, customData) 만 가진 _Sdf.Reference template 을 반환.

        - 이 template 의 LayerOffset 은 무시되며, sublayer 에 다시 author 할 때
          새로운 LayerOffset(offset, scale) 로 덮어쓴다.
        - 못 찾으면 registry.source_asset 으로 fallback.
        """
        if _Sdf is None or stage is None:
            return None
        try:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                return None
            for spec in prim.GetPrimStack():
                # 본인이 추가한 sublayer 의 spec 은 건너뛴다(자기 자신 참조 방지).
                try:
                    layer_id = spec.layer.identifier if spec.layer else ""
                except Exception:
                    layer_id = ""
                if layer_id and ("lam_inst_" in layer_id):
                    continue
                # 1순위 reference
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
                # 2순위 payload
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
        # registry fallback
        try:
            inst = self._registry.get_by_prim_path(prim_path)
            if inst is not None and getattr(inst, "source_asset", None):
                return ("ref", str(inst.source_asset), _Sdf.Path.emptyPath, {})
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
        kind, asset_path, prim_path_in_asset, custom_data = src

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
                sub_spec = sublayer.GetPrimAtPath(prim_path)
                if sub_spec is None:
                    sub_spec = _Sdf.CreatePrimInLayer(sublayer, _Sdf.Path(prim_path))
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
                p_now = stage.GetPrimAtPath(prim_path)
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
                    f"{_PRINT_PREFIX} sublayer mapping authored prim={prim_path} "
                    f"layer={sub_id} kind={kind} asset={asset_path} src_prim={prim_path_in_asset} "
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

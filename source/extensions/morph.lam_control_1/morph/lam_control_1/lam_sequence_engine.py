"""LAM 시퀀스 엔진.

step 종류: USD_TIMELINE / TIMESAMPLES_REPLAY / MOVE / ROTATE / DELAY /
SET_PRIM_VISIBILITY / PRIM_VISIBILITY (hide·show, sticky).

- `TIMESAMPLES_REPLAY` = **실무용** — Option E (offscreen stage + master mirror default write).
  멀티 인스턴스 독립 재생.
- `USD_TIMELINE` = **테스트용 (TBS 스타일)** — `omni.timeline` 으로 master stage 의 전역
  시각을 진행시켜 reference + OmniGraph 가 그대로 평가되도록 한다. 해당 step 동안만
  해당 인스턴스의 Option E micro-freeze 를 해제한다(`RuntimeEvaluator.begin_master_timeline_mode`).
  멀티 인스턴스 동시에 서로 다른 전역 시각을 가질 수는 없다(의도된 한계).
- `USD_TIMELINE` + `loop=True` 는 아직 master omni.timeline 루프를 지원하지 않으므로
  Option E 로 폴백한다(로그 안내).

【 호출 모델 】
- `LamSequenceRunner.run(steps)` 은 동기 차단형. background thread 에서 호출 권장
  (시퀀스 편집기·외부 이벤트 러너·JSON 테스트 창 모두 별도 thread 에서 호출).
- USD_TIMELINE step 은 `Scheduler.start()` 후 `estimated_duration_sec` 만큼 wall-clock
  sleep 으로 wait (loop=True 면 wait 없이 즉시 진행).
- MOVE/ROTATE step 은 LAM 측 animator 호출(=update tick subscription) 후 JSON ``duration``
  만큼 1차 대기한 뒤, **실제 보간이 끝날 때까지** 폴링한다(다음 step 이
  ``stop_prim_*`` 로 끊기지 않도록). TIMESAMPLES_REPLAY 는 ``inst.state != playing``
  까지 동일하게 대기한다.
- DELAY step 은 `time.sleep(duration / speed_scale)`.

【 그룹/지연 (TBS 와 동일) 】
- `run_with_previous = True` 인 step 들은 직전 step 과 같은 그룹.
- 그룹 = `[leader=a, ..., anchor=b]`. anchor 는 그룹 맨 아래 step.
- 그룹 시작 시 leader (a) 즉시 시작. follower (a+1..b) 는 각자 `step_delay_ms` 만큼
  background thread 에서 sleep 후 시작 (= 리더 시작 시점 기준 오프셋).
- anchor 의 종료(=duration 끝) 까지 wait → 다음 그룹 시작 전 다음 그룹 first step 의
  `step_delay_ms` 만큼 sleep (음수면 0 으로 클램프).

【 USD_TIMELINE 분기 】
- step["ref"] → `lam_id_resolver.resolve_step_ref()` → 매칭 성공 시 `Scheduler.start()`.
- 매칭 실패 시 RESOLVE_MISSING 표시 후 step skip (anchor 라면 wait 없이 진행).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# IMPORTANT — background thread 의 진입점. omni.usd / pxr import 가 그 thread 에서 처음
# 일어나면 main thread (MDL/RTX) 의 import lock 과 cross-wait 으로 deadlock 한다.
# 반드시 모듈 최상단(=메인 스레드의 모듈 로드 시점) 에서 미리 import 한다.
# 또한 본 모듈은 sequence step 실행 시 동적으로 다음 LAM 모듈들을 import 하는데,
# 그 모듈들도 사전에 캐시되어 있도록 여기서 미리 한 번 로드해 둔다.
import omni.usd as ou  # type: ignore  # noqa: E402,F401
from pxr import Gf  # type: ignore  # noqa: E402,F401

from .lam_hide_helper import LamHideController
from .lam_id_resolver import resolve_step_ref
from .lam_instance_registry import AnimationInstanceRegistry
from .lam_offset_correction import apply_world_space_offset_correction
from .lam_playback_scheduler import PlaybackScheduler
from .lam_types import LAM_FIXED_FPS, RESOLVE_MISSING, ResolveResult, StepRef
# 동적 import 의 lazy 로드를 막기 위해 미리 캐시 (background thread 호출 시 cache hit).
from . import lam_translate_animation as _ltx_preload  # type: ignore  # noqa: E402,F401
from . import lam_rotate_animation as _lrx_preload  # type: ignore  # noqa: E402,F401


_PRINT_PREFIX = "[LAM/SEQ]"
_runner_quiet_log: bool = False


def _seq_log(msg: str = "", **kwargs: Any) -> None:
    if _runner_quiet_log:
        return
    if msg:
        print(msg, **kwargs)


# step kind 상수 — 추후 dispatch / UI 가 공통으로 참조.
STEP_KIND_USD_TIMELINE: str = "USD_TIMELINE"
STEP_KIND_TIMESAMPLES_REPLAY: str = "TIMESAMPLES_REPLAY"
STEP_KIND_MOVE: str = "MOVE"
STEP_KIND_ROTATE: str = "ROTATE"
STEP_KIND_DELAY: str = "DELAY"
STEP_KIND_SET_PRIM_VISIBILITY: str = "SET_PRIM_VISIBILITY"
STEP_KIND_PRIM_VISIBILITY: str = "PRIM_VISIBILITY"
# 하위 호환·JSON 별칭 (에디터 ComboBox 는 PRIM_VISIBILITY 만 노출).
STEP_KIND_PRIM_HIDE: str = "PRIM_HIDE"
STEP_KIND_PRIM_SHOW: str = "PRIM_SHOW"

_PRIM_VISIBILITY_KINDS = frozenset({
    STEP_KIND_SET_PRIM_VISIBILITY,
    STEP_KIND_PRIM_VISIBILITY,
    STEP_KIND_PRIM_HIDE,
    STEP_KIND_PRIM_SHOW,
})


def step_kind_is_prim_visibility(kind: str) -> bool:
    """Imageable prim 의 sticky visibility (hide/show) step."""
    return (kind or "").upper() in _PRIM_VISIBILITY_KINDS


def prim_visibility_step_visible(step: dict) -> bool:
    """PRIM_VISIBILITY / SET_PRIM_VISIBILITY / PRIM_HIDE|SHOW → 표시 여부."""
    t = str(step.get("type") or "").upper()
    if t == STEP_KIND_PRIM_SHOW:
        return True
    if t in (STEP_KIND_PRIM_HIDE,):
        return False
    if t == STEP_KIND_SET_PRIM_VISIBILITY:
        return bool(step.get("visible", True))
    mode = str(step.get("mode", "hide") or "hide").strip().lower()
    return mode == "show"


# 두 step kind 모두 "ref 로 인스턴스를 지정하고 Option E 로 재생" 하는 의미를 가짐.
# 현재는 동일 핸들러를 공유 — 추후 USD_TIMELINE 의 TBS 방식 재구현 시 분기 필요.
_INSTANCE_PLAYBACK_KINDS = frozenset({
    STEP_KIND_USD_TIMELINE,
    STEP_KIND_TIMESAMPLES_REPLAY,
})


def step_kind_is_instance_playback(kind: str) -> bool:
    """`ref` 기반 인스턴스 재생 step 인가 (USD_TIMELINE / TIMESAMPLES_REPLAY)."""
    return (kind or "").upper() in _INSTANCE_PLAYBACK_KINDS


# --------------------------------------------------------------------- helper

def _group_end_index(steps: List[dict], a: int) -> int:
    """`a` 부터 `run_with_previous` 가 연속인 구간의 마지막 인덱스(=anchor)."""
    g_end = a
    while g_end + 1 < len(steps) and bool((steps[g_end + 1] or {}).get("run_with_previous", False)):
        g_end += 1
    return g_end


def _resolve_prim_paths(stage, prim_id: str) -> List[str]:
    """단일 prim 식별자 → 실제 prim path 목록.

    LAM 단순화: TBS 의 `resolve_prim_paths_multi` 가 지원하던 와일드카드/이름 검색은
    아직 미지원. 콤마 구분 절대 경로 / 단일 절대 경로만 지원.
    """
    if not prim_id:
        return []
    out: List[str] = []
    for token in str(prim_id).split(","):
        s = token.strip()
        if not s:
            continue
        if s.startswith("/") and stage is not None:
            try:
                p = stage.GetPrimAtPath(s)
                if p and p.IsValid():
                    out.append(s)
                else:
                    _seq_log(f"{_PRINT_PREFIX} prim_path not in stage: {s}", flush=True)
            except Exception:
                _seq_log(f"{_PRINT_PREFIX} prim_path resolve failed: {s}", flush=True)
        else:
            _seq_log(f"{_PRINT_PREFIX} only absolute /World/... paths supported, got: {s}", flush=True)
    return out


def _push_lam_stage_context(usd_context_name: Optional[str]) -> Optional[str]:
    from .lam_usd_stage_context import push_usd_context_name

    return push_usd_context_name(usd_context_name)


def _pop_lam_stage_context(prev: Optional[str]) -> None:
    from .lam_usd_stage_context import pop_usd_context_name

    pop_usd_context_name(prev)


def _stage():
    from .lam_usd_stage_context import get_stage_for_thread_context

    return get_stage_for_thread_context()


# --------------------------------------------------------- main-thread dispatch
#
# 배경 — LAM 은 무거운 외부 자산(예: 외부 https 머티리얼 참조 USD) 을 default 컨텍스트에
# 로드한다. main thread 가 MDL 컴파일/RTX 텍스처 fetch 로 USD stage write lock 을 잡고
# 있는 동안 background thread (시퀀스 러너) 에서 USD write 를 하면 lock cross-wait 으로
# 영구 deadlock 이 발생한다(진단 로그로 `_get_or_create_offset_translate_op` 의
# `AddTranslateOp` 에서 멈추는 것을 확인).
#
# TBS 는 USD load 직후 baseline 단계에서 main thread 가 미리 TBS_OFFSET op 를 author
# 해 두므로 background 에서는 read 만 하면 충분 → 이 문제가 발생하지 않는다.
# LAM 은 baseline 단계가 없어 첫 MOVE/ROTATE step 이 author 를 시도하므로 우회가 필요.
#
# 정책: USD write 가 일어나는 모든 호출(=`run_prim_translate_animation`,
# `run_prim_rotate_animation`, hide vis attribute set, offset correction,
# `_apply_start_snapshot`) 을 main thread 의 다음 update tick 으로 dispatch 한다.
# background thread 는 dispatch 만 하고 step duration 만큼 sleep → 다음 step 진행.
def _dispatch_main(fn: Callable[[], None]) -> None:
    """fn 을 다음 main update tick 에서 실행(fire-and-forget). background thread 안전.

    `create_subscription_to_pop` 자체는 thread-safe(주로 lock-free 또는 짧은 mutex 만)
    하다고 가정. 실제 fn() 은 main thread 의 update 콜백 안에서 실행되므로 USD write 가
    stage write lock 과 충돌하지 않는다.
    """
    box: Dict[str, Any] = {"sub": None}

    def _do(_e=None) -> None:
        try:
            fn()
        except Exception as exc:
            _seq_log(f"{_PRINT_PREFIX} dispatch_main fn failed: {exc}", flush=True)
        finally:
            try:
                if box["sub"] is not None:
                    box["sub"].unsubscribe()
            except Exception:
                pass
            box["sub"] = None

    try:
        import omni.kit.app as _kapp  # cache hit (lam_translate_animation 가 모듈 최상단에서 이미 로드).
        box["sub"] = _kapp.get_app().get_update_event_stream().create_subscription_to_pop(
            _do, name="morph.lam_control_1.sequence_engine.dispatch_main"
        )
    except Exception as exc:
        # fallback — Kit 가 없는 환경/테스트. 그냥 직접 호출 (이 환경에선 deadlock 도 없을 것).
        _seq_log(f"{_PRINT_PREFIX} dispatch_main fallback (direct call): {exc}", flush=True)
        _do(None)


def _dispatch_main_wait(fn: Callable[[], None], *, timeout: float = 15.0) -> bool:
    """다음 main update 프레임에서 `fn()` 실행이 완료될 때까지 대기.

    Run(reset) 시 TBS_OFFSET 초기화처럼 background thread 에서 반드시 main-thread USD write
    완료 후 다음 로직으로 진행해야 할 때 사용한다.
    """
    done = threading.Event()
    err: List[Optional[BaseException]] = [None]

    def wrapped() -> None:
        try:
            fn()
        except BaseException as e:
            err[0] = e
        finally:
            done.set()

    _dispatch_main(wrapped)
    ok = done.wait(timeout=float(timeout))
    if not ok:
        _seq_log(f"{_PRINT_PREFIX} _dispatch_main_wait TIMEOUT after {timeout}s", flush=True)
        return False
    if err[0] is not None:
        raise err[0]
    return True


def _collect_prim_paths_for_reset(steps: List[dict]) -> List[str]:
    """시퀀스에 등장하는 prim.

    MOVE/ROTATE 의 prim 필드 + (USD_TIMELINE / TIMESAMPLES_REPLAY) 의 ref.prim_path.
    """
    out: List[str] = []
    seen: set[str] = set()
    st = _stage()
    for step in steps:
        if not step:
            continue
        t = str(step.get("type") or "").upper()
        if t in (STEP_KIND_MOVE, STEP_KIND_ROTATE):
            for p in _resolve_prim_paths(st, str(step.get("prim") or "")):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        elif step_kind_is_prim_visibility(t):
            for p in _resolve_prim_paths(st, str(step.get("prim") or "")):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        elif step_kind_is_instance_playback(t):
            ref = StepRef.from_dict(step.get("ref"))
            pp = (ref.prim_path or "").strip()
            if pp.startswith("/") and pp not in seen:
                seen.add(pp)
                out.append(pp)
    return out


def _refresh_instance_asset_time_from_stage(instance) -> tuple[float, float, float]:
    """instance 의 asset_start/end/tps 가 (0,0) 이면 prim 산하 timeSamples 에서 폴백 추출.

    `read_asset_time_range` 는 자산 USD 의 root metadata (`startTimeCode/endTimeCode`) 만
    읽는데, FBX→USD 등 일부 변환 결과는 root metadata 가 비어 있고 timeSamples 만 있다.
    그 경우 (0,0) 으로 등록되어 LAM USD_TIMELINE 의 estimated duration 이 0 이 된다.
    이 함수는 해당 케이스를 살리기 위한 폴백 — prim 산하 모든 attribute 의 timeSamples
    범위에서 (min, max) 를 best-effort 로 뽑아 instance 에 채운다.

    반환: (asset_start, asset_end, asset_tps) — 갱신 후 값.
    """
    s = float(getattr(instance, "asset_start_time", 0.0) or 0.0)
    e = float(getattr(instance, "asset_end_time", 0.0) or 0.0)
    tps = float(getattr(instance, "asset_tps", 0.0) or 0.0)
    if e > s and tps > 0:
        return (s, e, tps)
    stage = _stage()
    if stage is None:
        return (s, e, tps)
    prim_path = str(getattr(instance, "prim_path", "") or "")
    if not prim_path.startswith("/"):
        return (s, e, tps)
    try:
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return (s, e, tps)
    except Exception:
        return (s, e, tps)

    mn: Optional[float] = None
    mx: Optional[float] = None
    n_attr = 0
    try:
        stack = [prim]
        seen: set = set()
        while stack:
            p = stack.pop()
            try:
                pp = str(p.GetPath())
            except Exception:
                continue
            if pp in seen:
                continue
            seen.add(pp)
            try:
                for ch in p.GetAllChildren():
                    stack.append(ch)
            except Exception:
                pass
            try:
                attrs = p.GetAuthoredAttributes()
            except Exception:
                attrs = []
            for attr in attrs:
                try:
                    n = attr.GetNumTimeSamples()
                except Exception:
                    continue
                if n <= 0:
                    continue
                try:
                    samples = attr.GetTimeSamples()
                except Exception:
                    samples = []
                if not samples:
                    continue
                a = float(samples[0])
                b = float(samples[-1])
                mn = a if (mn is None or a < mn) else mn
                mx = b if (mx is None or b > mx) else mx
                n_attr += 1
    except Exception as exc:
        _seq_log(f"{_PRINT_PREFIX} fallback timeSamples scan failed prim={prim_path}: {exc}", flush=True)

    if mn is not None and mx is not None and mx > mn:
        # FPS 30 고정 정책 — tps 는 항상 LAM_FIXED_FPS.
        tps = LAM_FIXED_FPS
        try:
            instance.asset_start_time = float(mn)
            instance.asset_end_time = float(mx)
            instance.asset_tps = float(tps)
        except Exception:
            pass
        _seq_log(
            f"{_PRINT_PREFIX} fallback asset timeline filled prim={prim_path} "
            f"timeSamples [{mn},{mx}]@{tps}fps(forced)  (n_attr_with_samples={n_attr})",
            flush=True,
        )
        return (float(mn), float(mx), float(tps))

    _seq_log(
        f"{_PRINT_PREFIX} fallback found NO timeSamples prim={prim_path} "
        f"(자산이 정적이거나 reference 가 비어있을 수 있음). instance asset_time stays "
        f"[{s},{e}]@{tps}fps",
        flush=True,
    )
    return (s, e, tps)


def _wrap_to_180(deg: float) -> float:
    """각도 차이를 (-180, 180] 범위로 정규화 — 최단 회전 호.

    예) `_wrap_to_180(-100) == -100` (이미 [-180,180]),
        `_wrap_to_180(260) == -100`  (시계방향으로 100° 가 시계반대 260° 보다 짧다),
        `_wrap_to_180(-260) == 100`.
    정확히 ±180° 인 경우 +180 으로 통일해 방향 일관성을 유지한다.
    """
    try:
        d = float(deg)
    except Exception:
        return 0.0
    d = (d + 180.0) % 360.0 - 180.0
    if abs(d + 180.0) < 1e-9:
        return 180.0
    return d


def _reset_tbs_offset_ops_for_paths(
    paths: List[str],
    *,
    usd_context_name: Optional[str] = None,
) -> None:
    """애니메이션 중지 후 `TBS_OFFSET` Translate/Rotate 를 0 으로 (ctx 스코프)."""
    from . import lam_translate_animation as _ltx
    from . import lam_rotate_animation as _lrx

    ctx = usd_context_name
    try:
        _lrx.stop_world_pivot_rotate_animation()
    except Exception:
        pass
    for p in paths:
        try:
            _ltx.stop_prim_translate_animation(p, ctx)
            _lrx.stop_prim_rotate_animation(p, ctx)
        except Exception:
            pass
        try:
            _ltx.zero_tbs_offset_translate_at_path(p, usd_context_name=ctx)
            _lrx.zero_tbs_offset_rotate_at_path(p, usd_context_name=ctx)
        except Exception as exc:
            _seq_log(f"{_PRINT_PREFIX} zero TBS_OFFSET failed path={p}: {exc}", flush=True)


# --------------------------------------------------------------------- runner

def _playback_speed_scale(fallback: float) -> float:
    """CSV Play 중이면 UI 라이브 배속, 아니면 ``fallback``."""
    try:
        from .simulation_play import (
            csv_play_session_active,
            get_csv_play_live_speed_scale,
            sync_csv_play_live_speed_from_ui,
        )

        if csv_play_session_active():
            sync_csv_play_live_speed_from_ui()
            return get_csv_play_live_speed_scale()
    except Exception:
        pass
    return float(max(0.01, fallback or 1.0))


def _csv_play_nominal_sleep(runner: "LamSequenceRunner", wall_sec: float, *, allow_stop: bool) -> None:
    """CSV Play: wall 대기를 JSON 시간 기준으로 환산해 배속 변경에 따라 가변 대기."""
    if wall_sec <= 1e-9:
        return
    sp0 = _playback_speed_scale(1.0)
    nominal_remaining = float(wall_sec) * sp0
    while nominal_remaining > 1e-6:
        if allow_stop and runner._stop_flag.is_set():
            return
        sp = _playback_speed_scale(sp0)
        wall_chunk = min(0.05, nominal_remaining / sp)
        time.sleep(wall_chunk)
        nominal_remaining -= wall_chunk * sp


class LamSequenceRunner:
    """1 시퀀스의 step 배열을 순차 실행하는 러너.

    background thread 에서 동기 호출 권장. main thread 에서 호출하면 UI 가 freeze 한다.
    """

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        on_step_resolved: Optional[Callable[[int, dict, ResolveResult], None]] = None,
        *,
        usd_context_name: Optional[str] = None,
        play_screen: Optional[int] = None,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._on_step_resolved = on_step_resolved
        self._usd_context_name: Optional[str] = (
            str(usd_context_name).strip() if usd_context_name else None
        ) or None
        try:
            self._play_screen: int = max(1, int(play_screen or 1))
        except Exception:
            self._play_screen = 1
        self._stop_flag = threading.Event()
        self._hide = LamHideController(usd_context_name=self._usd_context_name)
        # 첫 step 메타 (TBS 와 동일 schema 호환)
        self._start_from_current: bool = False
        self._start_from_current_paths: List[str] = []
        self._start_snapshot: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ public

    def stop(self, *, cancel_all_move_rotate: bool = True) -> None:
        """진행 중인 시퀀스 중단(다음 sleep 단계에서 빠져나옴).

        Args:
            cancel_all_move_rotate: True(기본) — 시퀀스 편집기 Stop 과 동일하게 전역
                translate/rotate 애니메이션을 모두 중단한다.
                False — `_stop_flag` 만 세워 루프만 빠져나가게 하고, 전역
                `stop_all_*` 는 호출하지 않는다. `lam_json_test_window` 가 동일
                prim / 인스턴스 충돌 시 선점(preempt) 할 때 사용한다(다른 JSON 의
                MOVE/ROTATE 를 건드리지 않기 위함).
        """
        self._stop_flag.set()
        if cancel_all_move_rotate:
            try:
                from . import lam_rotate_animation as _lrx
                from . import lam_translate_animation as _ltx

                if self._usd_context_name:
                    _ltx.stop_translate_animations_for_context(self._usd_context_name)
                    _lrx.stop_rotate_animations_for_context(self._usd_context_name)
                else:
                    _ltx.stop_all_translate_animations()
                    _lrx.stop_all_rotate_animations()
            except Exception:
                pass
        try:
            self._hide.clear_all()
        except Exception:
            pass

    def run(
        self,
        steps: List[dict],
        *,
        reset_each_start: bool = False,
        speed_scale: float = 1.0,
        on_complete: Optional[Callable[[], None]] = None,
        quiet: bool = False,
    ) -> None:
        """동기 차단형 시퀀스 실행. background thread 에서 호출 권장."""
        global _runner_quiet_log
        prev_quiet = _runner_quiet_log
        _runner_quiet_log = _runner_quiet_log or bool(quiet)
        prev_ctx = _push_lam_stage_context(self._usd_context_name)
        try:
            self._stop_flag.clear()
            steps = list(steps or [])
            if not steps:
                if on_complete:
                    try:
                        on_complete()
                    except Exception:
                        pass
                return

            sp = float(max(0.01, speed_scale or 1.0))

            if reset_each_start:
                rpaths = _collect_prim_paths_for_reset(steps)
                _seq_log(
                    f"{_PRINT_PREFIX} reset_each_start: zero TBS_OFFSET for {len(rpaths)} prim(s)",
                    flush=True,
                )
                try:
                    _dispatch_main_wait(
                        lambda paths=rpaths, c=self._usd_context_name: _reset_tbs_offset_ops_for_paths(
                            paths, usd_context_name=c
                        ),
                        timeout=15.0,
                    )
                except Exception as exc:
                    _seq_log(f"{_PRINT_PREFIX} reset TBS_OFFSET failed: {exc}", flush=True)

            first = steps[0] or {}
            self._start_from_current = bool(first.get("_start_from_current", False))
            raw_paths = str(first.get("_start_from_current_paths", "") or "").strip()
            self._start_from_current_paths = [
                t.strip() for t in raw_paths.split(",") if t.strip()
            ]
            self._start_snapshot = self._parse_start_snapshot(first.get("_start_snapshot", {}))
            if self._start_snapshot:
                try:
                    self._apply_start_snapshot(self._start_snapshot)
                except Exception as exc:
                    _seq_log(f"{_PRINT_PREFIX} _apply_start_snapshot failed: {exc}", flush=True)

            d0_ms = int(first.get("step_delay_ms", 0) or 0)
            sp = _playback_speed_scale(sp)
            d0 = max(0.0, (d0_ms / 1000.0) / sp)
            if d0 > 0:
                self._sleep(d0)

            a = 0
            while a < len(steps):
                if self._stop_flag.is_set():
                    _seq_log(f"{_PRINT_PREFIX} stop requested at step[{a}]", flush=True)
                    break
                sp = _playback_speed_scale(sp)
                b = _group_end_index(steps, a)
                self._execute_group(steps, a, b, sp, reset_each_start)
                next_idx = b + 1
                if next_idx < len(steps):
                    sp = _playback_speed_scale(sp)
                    delay_ms_next = int((steps[next_idx] or {}).get("step_delay_ms", 0) or 0)
                    delay_next = max(0.0, (delay_ms_next / 1000.0) / sp)
                    if delay_next > 0:
                        self._sleep(delay_next)
                a = next_idx

            try:
                self._hide.clear_all()
            except Exception:
                pass

            if on_complete:
                try:
                    on_complete()
                except Exception:
                    pass
        finally:
            _pop_lam_stage_context(prev_ctx)
            _runner_quiet_log = prev_quiet

    # ------------------------------------------------------------------ group

    def _execute_group(
        self,
        steps: List[dict],
        a: int,
        b: int,
        speed_scale: float,
        reset_each_start: bool,
    ) -> None:
        """그룹 [a..b] 실행: leader 즉시, follower 는 step_delay_ms 만큼 지연 후 시작.

        anchor 의 estimated duration 1차 대기 후, 그룹에 포함된 MOVE/ROTATE/TIMESAMPLES 가
        **실제로 끝날 때까지** 추가 폴링한다(다음 step 이 진행 중 anim 을 stop 하지 않도록).
        """
        if self._stop_flag.is_set():
            return
        sp = _playback_speed_scale(speed_scale)
        leader_idx = a
        anchor_idx = b
        t_group_start = time.monotonic()
        motion_tx, motion_rot, motion_replay = self._collect_motion_targets_from_steps(steps, a, b)
        motion_extra_timeout = self._estimate_group_motion_extra_timeout(
            steps, a, b, sp
        )

        # leader 즉시 시작.
        leader_dur = self._start_step(leader_idx, steps[leader_idx], sp, reset_each_start)
        follower_threads: List[threading.Thread] = []
        if leader_idx == anchor_idx:
            self._wait_for(t_group_start + leader_dur)
        else:
            anchor_finish_at_holder: Dict[str, float] = {"t": t_group_start + leader_dur}

            for i in range(a + 1, b + 1):
                step_i = steps[i] or {}

                def _runner_for(idx: int = i, step: dict = step_i) -> None:
                    sp_follow = _playback_speed_scale(sp)
                    delay = max(
                        0.0,
                        (int(step.get("step_delay_ms", 0) or 0) / 1000.0) / sp_follow,
                    )
                    if delay > 0:
                        self._sleep(delay, allow_stop=True)
                    if self._stop_flag.is_set():
                        return
                    start_at = time.monotonic()
                    sp_step = _playback_speed_scale(sp)
                    dur = self._start_step(idx, step, sp_step, reset_each_start)
                    if idx == anchor_idx:
                        anchor_finish_at_holder["t"] = start_at + dur

                t = threading.Thread(
                    target=_runner_for, name=f"lam_seq_follower_{i}", daemon=True
                )
                t.start()
                follower_threads.append(t)

            anchor_step = steps[anchor_idx] or {}
            sp_anchor = _playback_speed_scale(sp)
            anchor_delay_sec = max(
                0.0, (int(anchor_step.get("step_delay_ms", 0) or 0) / 1000.0) / sp_anchor
            )
            self._sleep(anchor_delay_sec + 0.05, allow_stop=True)
            self._wait_for(anchor_finish_at_holder["t"])
            join_timeout = motion_extra_timeout + 5.0
            deadline = time.monotonic() + join_timeout
            for ft in follower_threads:
                while ft.is_alive():
                    if self._stop_flag.is_set():
                        return
                    if time.monotonic() >= deadline:
                        break
                    ft.join(timeout=0.1)

        self._wait_for_motion_complete(
            motion_tx,
            motion_rot,
            motion_replay,
            max_extra_sec=motion_extra_timeout,
        )

    def _collect_motion_targets_from_steps(
        self, steps: List[dict], a: int, b: int
    ) -> Tuple[List[str], List[str], List[str]]:
        """그룹 step 에서 MOVE/ROTATE prim 경로와 TIMESAMPLES 인스턴스 prim 을 수집."""
        tx: List[str] = []
        rot: List[str] = []
        replay: List[str] = []
        stage = _stage()
        for i in range(a, b + 1):
            step = steps[i] or {}
            t = str(step.get("type") or "").upper()
            if t == STEP_KIND_MOVE:
                tx.extend(_resolve_prim_paths(stage, str(step.get("prim") or "")))
            elif t == STEP_KIND_ROTATE:
                rot.extend(_resolve_prim_paths(stage, str(step.get("prim") or "")))
            elif step_kind_is_instance_playback(t):
                ref = StepRef.from_dict(step.get("ref"))
                result = resolve_step_ref(self._registry.all_instances(), ref)
                if result.instance is not None:
                    replay.append(str(result.instance.prim_path))

        def _dedupe(paths: List[str]) -> List[str]:
            seen: set[str] = set()
            out: List[str] = []
            for p in paths:
                if p and p not in seen:
                    seen.add(p)
                    out.append(p)
            return out

        return _dedupe(tx), _dedupe(rot), _dedupe(replay)

    def _estimate_group_motion_extra_timeout(
        self, steps: List[dict], a: int, b: int, speed_scale: float
    ) -> float:
        """그룹 motion 완료 폴링 상한 [s] — JSON duration 합의 1.5배 + 여유."""
        sp = float(max(0.01, speed_scale or 1.0))
        total = 0.0
        for i in range(a, b + 1):
            step = steps[i] or {}
            t = str(step.get("type") or "").upper()
            if t in (STEP_KIND_MOVE, STEP_KIND_ROTATE, STEP_KIND_DELAY):
                total += float(step.get("duration", 0.0) or 0.0) / sp
            elif step_kind_is_instance_playback(t):
                ref = StepRef.from_dict(step.get("ref"))
                result = resolve_step_ref(self._registry.all_instances(), ref)
                if result.instance is not None:
                    play = step.get("play") or {}
                    s_frame = play.get("start_frame", step.get("start_frame"))
                    e_frame = play.get("end_frame", step.get("end_frame"))
                    if s_frame is not None and e_frame is not None:
                        rng_mode = "frames"
                        rng_start = float(s_frame)
                        rng_end = float(e_frame)
                    else:
                        rng_mode = str(play.get("range_mode", step.get("range_mode", "full")))
                        rng_start = float(
                            play.get("range_start", step.get("range_start", 0.0)) or 0.0
                        )
                        rng_end = float(
                            play.get("range_end", step.get("range_end", 0.0)) or 0.0
                        )
                    per_sp = float(
                        max(
                            0.01,
                            float(play.get("speed_scale", step.get("speed_scale", 1.0)) or 1.0)
                            * sp,
                        )
                    )
                    total += self._estimate_usd_timeline_duration(
                        result.instance,
                        range_mode=rng_mode,
                        range_start=rng_start,
                        range_end=rng_end,
                        combined_speed=per_sp,
                    )
                else:
                    total += 5.0
            total += max(0.0, int(step.get("step_delay_ms", 0) or 0) / 1000.0) / sp
        return min(300.0, max(2.0, total * 1.5 + 2.0))

    def _wait_for_motion_complete(
        self,
        translate_paths: List[str],
        rotate_paths: List[str],
        replay_prims: List[str],
        *,
        max_extra_sec: float,
    ) -> None:
        """추정 duration 대기 후에도 anim/replay 가 살아 있으면 완료까지 폴링."""
        if not translate_paths and not rotate_paths and not replay_prims:
            return

        deadline = time.monotonic() + max(0.5, float(max_extra_sec))
        poll = 0.033

        def _any_busy() -> bool:
            ctx_nm = self._usd_context_name
            for p in translate_paths:
                if _ltx_preload.is_prim_translate_animation_running(p, ctx_nm):
                    return True
            for p in rotate_paths:
                if _lrx_preload.is_prim_rotate_animation_running(p, ctx_nm):
                    return True
            for rp in replay_prims:
                inst = self._registry.get_by_prim_path(rp)
                if inst is not None and str(inst.state) == "playing":
                    return True
            return False

        while not self._stop_flag.is_set():
            try:
                from .simulation_play import sync_csv_play_live_speed_from_ui

                sync_csv_play_live_speed_from_ui()
            except Exception:
                pass
            if not _any_busy():
                return
            if time.monotonic() >= deadline:
                _seq_log(
                    f"{_PRINT_PREFIX} motion complete wait timeout "
                    f"(tx={len(translate_paths)} rot={len(rotate_paths)} "
                    f"replay={len(replay_prims)})",
                    flush=True,
                )
                return
            self._sleep(poll, allow_stop=True)

    # ------------------------------------------------------------------ step

    def _start_step(
        self,
        idx: int,
        step: dict,
        speed_scale: float,
        reset_each_start: bool,
    ) -> float:
        """step 한 개를 시작하고 estimated duration(초) 반환. blocking 하지 않음."""
        if self._stop_flag.is_set():
            return 0.0
        t = str(step.get("type") or "").upper()
        # hide 적용 (TBS 와 동일 — step 시작 시 invisible, duration 후 unhide 예약).
        # 주의: hide_for_step 안의 vis attribute set 은 USD write 다. background thread 에서
        # 호출되면 _start_move 처럼 deadlock 위험이 있다(현재는 default OFF 이므로 보류,
        # 사용자가 hide_enabled 를 켜기 시작하면 같은 _dispatch_main 패턴으로 마이그레이션).
        hidden_paths = self._hide.hide_for_step(
            bool(step.get("hide_enabled", False)),
            str(step.get("hide_prims", "") or ""),
        )
        duration = 0.0
        try:
            if step_kind_is_instance_playback(t):
                # USD_TIMELINE = master omni.timeline (테스트) / TIMESAMPLES_REPLAY = Option E (실무).
                duration = self._start_usd_timeline(idx, step, speed_scale, reset_each_start)
            elif t == STEP_KIND_MOVE:
                duration = self._start_move(idx, step, speed_scale)
            elif t == STEP_KIND_ROTATE:
                duration = self._start_rotate(idx, step, speed_scale)
            elif t == STEP_KIND_DELAY:
                # DELAY 는 caller 에서 wait 하므로 여기서는 sleep 안 한다.
                sp = float(max(0.01, speed_scale or 1.0))
                duration = float(step.get("duration", 1.0) or 1.0) / sp
            elif step_kind_is_prim_visibility(t):
                duration = self._start_set_prim_visibility(idx, step, speed_scale)
            else:
                _seq_log(f"{_PRINT_PREFIX} step[{idx}] unknown type={t!r}", flush=True)
        except Exception as exc:
            _seq_log(f"{_PRINT_PREFIX} step[{idx}] {t} failed: {exc}", flush=True)
        finally:
            # duration 이 끝난 뒤 hide 해제. 작은 지연(0.2s) 으로 step 경계 깜빡임 방지.
            if hidden_paths:
                self._schedule_unhide_after(hidden_paths, duration)
        return duration

    # -------------------------------------------------------- USD_TIMELINE / TIMESAMPLES_REPLAY

    def _start_usd_timeline(
        self,
        idx: int,
        step: dict,
        speed_scale: float,
        reset_each_start: bool,
    ) -> float:
        # 로그 출력용 — 두 kind 가 같은 핸들러를 공유하므로 log line 에 실제 step type 을 출력.
        step_kind_label = (str(step.get("type") or "") or STEP_KIND_USD_TIMELINE).upper()

        ref = StepRef.from_dict(step.get("ref"))
        play = step.get("play") or {}

        # mode/start_frame/end_frame/speed_scale 는 TBS 와 동일하지만 LAM 인스턴스 모델에선
        # play 블록이 우선. play 가 없으면 step 최상위 키(start_frame, end_frame, speed_scale)
        # 를 fallback 으로 사용한다(편집기에서 새로 만든 step 호환).
        def _val(key: str, default):
            if key in play and play[key] is not None:
                return play[key]
            if key in step and step[key] is not None:
                return step[key]
            return default

        result = resolve_step_ref(self._registry.all_instances(), ref)
        if self._on_step_resolved is not None:
            try:
                self._on_step_resolved(idx, step, result)
            except Exception:
                pass

        if result.status == RESOLVE_MISSING or result.instance is None:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] {step_kind_label} MISSING ref={ref.to_dict()}",
                flush=True,
            )
            return 0.0

        if result.updated_ref is not None:
            step["ref"] = result.updated_ref.to_dict()

        # 진단 — 매칭된 인스턴스의 자산 timeline. 이게 [0,0] 이면 estimated duration 이 0 이
        # 되어 USD_TIMELINE step 이 즉시 끝나 보인다(=재생 안 보임).
        try:
            inst_dbg = result.instance
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] {step_kind_label} inst prim={inst_dbg.prim_path} "
                f"asset_time=[{inst_dbg.asset_start_time},{inst_dbg.asset_end_time}]"
                f"@{inst_dbg.asset_tps}fps "
                f"current_state={inst_dbg.state} virtual_time={inst_dbg.virtual_time:.3f}s "
                f"speed={inst_dbg.speed} loop={inst_dbg.loop}",
                flush=True,
            )
        except Exception as _exc:
            _seq_log(f"{_PRINT_PREFIX} step[{idx}] inst dbg print failed: {_exc}", flush=True)

        # 폴백 — 자산 timeline 이 (0,0) 이면 prim 산하 timeSamples 에서 추출 시도.
        # 메인 스레드에서 USD read 가 일어나도록 dispatch_main_wait 으로 안전하게.
        try:
            if not (
                result.instance.asset_end_time > result.instance.asset_start_time
                and result.instance.asset_tps > 0
            ):
                _dispatch_main_wait(
                    lambda: _refresh_instance_asset_time_from_stage(result.instance),
                    timeout=5.0,
                )
        except Exception as exc:
            _seq_log(f"{_PRINT_PREFIX} step[{idx}] asset timeline fallback failed: {exc}", flush=True)

        # offset_correction (REQ-011, TBS 와 동일 의미). 기본 OFF.
        if bool(step.get("offset_correction_enabled", False)):
            try:
                paths_for_offset: List[str] = [result.instance.prim_path]
                extra = str(step.get("offset_correct_prims", "") or "").strip()
                if extra:
                    stage = _stage()
                    for p in _resolve_prim_paths(stage, extra):
                        if p not in paths_for_offset:
                            paths_for_offset.append(p)
                start_seconds = float(_val("start_frame", 0) or 0.0)
                # FPS 30 고정 정책 — 자산 tps 무시.
                tps = LAM_FIXED_FPS
                start_seconds_in = start_seconds / float(tps) if start_seconds > 0 else 0.0
                apply_world_space_offset_correction(
                    paths_for_offset, start_seconds_in, asset_tps=tps
                )
            except Exception as exc:
                _seq_log(f"{_PRINT_PREFIX} step[{idx}] offset_correction failed: {exc}", flush=True)

        per_step_speed = float(_val("speed_scale", 1.0) or 1.0)
        per_step_speed = float(max(0.01, per_step_speed))
        combined_speed = float(max(0.01, per_step_speed * float(max(0.01, speed_scale or 1.0))))
        loop = bool(_val("loop", False))

        # range 모드: TBS sequence_editor 는 MANUAL + start_frame/end_frame 만 사용.
        # LAM 도 동일 — start_frame/end_frame 가 있으면 frames 모드, 없으면 full.
        s_frame = _val("start_frame", None)
        e_frame = _val("end_frame", None)
        if s_frame is not None and e_frame is not None and int(e_frame) > int(s_frame):
            range_mode = "frames"
            range_start = float(s_frame)
            range_end = float(e_frame)
        else:
            range_mode = str(_val("range_mode", "full"))
            range_start = float(_val("range_start", _val("ratio_start", 0.0)) or 0.0)
            range_end = float(_val("range_end", _val("ratio_end", 0.0)) or 0.0)

        est = self._estimate_usd_timeline_duration(
            result.instance,
            range_mode=range_mode,
            range_start=range_start,
            range_end=range_end,
            combined_speed=combined_speed,
        )

        # ------------------------------------------------------------------ USD_TIMELINE (TBS) — omni.timeline + master stage 전역 시각
        if step_kind_label == STEP_KIND_USD_TIMELINE and not loop:
            prim_path = result.instance.prim_path
            if range_mode == "frames":
                play_sf, play_ef = float(range_start), float(range_end)
            else:
                play_sf = float(result.instance.asset_start_time)
                play_ef = float(result.instance.asset_end_time)
            if play_ef <= play_sf:
                _seq_log(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE skip — invalid frame range "
                    f"[{play_sf},{play_ef}]",
                    flush=True,
                )
                return 0.0

            snap_holder: Dict[str, Any] = {}

            def _snap_tl() -> None:
                from .lam_master_timeline_play import snapshot_timeline

                snap_holder["v"] = snapshot_timeline()

            _dispatch_main_wait(_snap_tl, timeout=5.0)
            snap = snap_holder.get("v") or (None, 0.0, False, None)
            tl_snap, saved_time, was_playing, prev_speed = snap

            def _tl_begin() -> None:
                ok_b = self._scheduler.begin_master_timeline_mode(prim_path)
                from .lam_master_timeline_play import begin_play_frame_range

                ok_p = begin_play_frame_range(
                    start_frame=play_sf,
                    end_frame=play_ef,
                    speed_scale=combined_speed,
                    fps=LAM_FIXED_FPS,
                )
                _seq_log(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE master-timeline "
                    f"prim={prim_path} frames=[{play_sf},{play_ef}] "
                    f"begin_ok={ok_b} play_ok={ok_p}",
                    flush=True,
                )

            try:
                _dispatch_main_wait(_tl_begin, timeout=15.0)
            except Exception as exc:
                _seq_log(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE begin failed: {exc}",
                    flush=True,
                )
                return 0.0

            self._sleep(est, allow_stop=True)

            # 사용자 요청 (2026-05-13) — "타임라인을 막는 코드를 모두 제거" 정책에 맞춰
            # step 종료 후에도 freeze override 는 author 하지 않는다. step 이 끝나면:
            #   (1) omni.timeline 을 끝 시간 (= play_ef/fps) 에서 pause + 그 위치 유지
            #       → reference / OmniGraph 가 그 시각을 평가하여 viewport 가 끝 자세
            #         그대로 머무른다 (정지 직후 시점).
            #   (2) 사용자가 timeline 슬라이더를 움직이면 prim 도 자유롭게 따라간다 —
            #       freeze 를 박지 않으므로 timeline 재생이 막히지 않는다.
            #   (3) ``end_master_timeline_mode`` 의 ``freeze_at_tc`` 인자는 호환을 위해
            #       전달하되 evaluator 측에서 무시한다.
            end_time_sec = float(play_ef) / float(LAM_FIXED_FPS)
            end_tc = float(play_ef)

            def _tl_end() -> None:
                from .lam_master_timeline_play import (
                    end_play_pause,
                    restore_timeline_after_usd_timeline,
                )

                end_play_pause()
                self._scheduler.end_master_timeline_mode(
                    prim_path, freeze_at_tc=end_tc
                )
                # saved_time 이 아니라 끝 시간으로 복구 → 사용자가 step 종료 후
                # timeline 슬라이더에서 그 위치를 그대로 본다.
                restore_timeline_after_usd_timeline(
                    tl_snap, end_time_sec, False, prev_speed
                )

            try:
                _dispatch_main_wait(_tl_end, timeout=15.0)
            except Exception as exc:
                _seq_log(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE end failed: {exc}",
                    flush=True,
                )

            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE matched_by={result.matched_by} "
                f"prim={prim_path} range=frames[{play_sf},{play_ef}] "
                f"sp={combined_speed} loop={loop} mode=MASTER_TIMELINE est_duration={est:.3f}s "
                f"asset_time=[{result.instance.asset_start_time},{result.instance.asset_end_time}]"
                f"@{LAM_FIXED_FPS}fps(forced)",
                flush=True,
            )
            return 0.0

        if step_kind_label == STEP_KIND_USD_TIMELINE and loop:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE loop=True — Option E 로 폴백 "
                f"(master omni.timeline 루프는 미구현)",
                flush=True,
            )

        # ------------------------------------------------------------------ TIMESAMPLES_REPLAY (또는 USD_TIMELINE+loop) — Option E
        # 2026-05-13 사용자 합의 (`타임라인을 막는 코드를 모두 제거`):
        #   - LayerOffset freeze / OmniGraph deactivate 같은 sublayer override 는 일절
        #     author 하지 않는다. evaluator 가 instance sublayer (stronger) 에 박는
        #     default 값이 reference (weaker) 의 timeSamples 를 마스킹하는 USD value
        #     resolution 만으로 step 재생이 성립한다.
        #   - 본 step 시작 시 `begin_replay_mode` 가 evaluator 의 default 쓰기를
        #     **활성화**한다 (해당 prim 을 `_evaluator_active_prims` 에 추가). 그 결과
        #     매 update tick `evaluate_and_write(virtual_time)` 가 inst sublayer 에
        #     default 를 박아 viewport 가 그 자세로 보인다.
        #   - step 종료 시 default 쓰기를 끄지 **않는다** — viewport 가 마지막 vt 의
        #     자세를 그대로 유지해야 한다는 사용자 요구. evaluator 가 그 vt 값을 계속
        #     default 로 박아 끝 자세를 잠금.
        #   - 사용자가 Reset 을 누르면 sequence_editor 가 `end_replay_mode` 를 호출해
        #     default 쓰기를 끄고 inst sublayer 의 default opinion 도 청소 → reference
        #     의 timeSamples 가 다시 winner 가 되어 master timeline 자유 재생 가능.
        # scheduler.start 도 main thread 에서 실행 (USD attribute 평가 lock 보호).
        replay_prim = result.instance.prim_path

        def _do_begin_replay_in_main() -> None:
            try:
                self._scheduler.begin_replay_mode(replay_prim)
            except Exception as exc:
                _seq_log(
                    f"{_PRINT_PREFIX} step[{idx}] begin_replay_mode failed prim={replay_prim}: {exc}",
                    flush=True,
                )

        try:
            _dispatch_main_wait(_do_begin_replay_in_main, timeout=10.0)
        except Exception as exc:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] begin_replay dispatch failed: {exc}",
                flush=True,
            )

        start_ok_holder: Dict[str, bool] = {"ok": False}

        def _do_start_in_main() -> None:
            start_ok_holder["ok"] = bool(
                self._scheduler.start(
                    replay_prim,
                    reset=reset_each_start,
                    speed=combined_speed,
                    loop=loop,
                    range_mode=range_mode,
                    range_start=range_start,
                    range_end=range_end,
                )
            )

        try:
            _dispatch_main_wait(_do_start_in_main, timeout=10.0)
        except Exception as exc:
            _seq_log(f"{_PRINT_PREFIX} step[{idx}] scheduler.start failed: {exc}", flush=True)
        ok = start_ok_holder["ok"]

        _seq_log(
            f"{_PRINT_PREFIX} step[{idx}] {step_kind_label} matched_by={result.matched_by} "
            f"prim={replay_prim} range={range_mode}[{range_start},{range_end}] "
            f"sp={combined_speed} loop={loop} ok={ok} est_duration={est:.3f}s "
            f"asset_time=[{result.instance.asset_start_time},{result.instance.asset_end_time}]"
            f"@{LAM_FIXED_FPS}fps(forced)",
            flush=True,
        )
        if not ok or loop:
            # 시작 실패 시 freeze 도 즉시 해제 (계속 박혀 있으면 viewport 가 멈춤).
            if not ok:

                def _do_end_replay_fail() -> None:
                    try:
                        self._scheduler.end_replay_mode(replay_prim)
                    except Exception:
                        pass

                try:
                    _dispatch_main_wait(_do_end_replay_fail, timeout=5.0)
                except Exception:
                    pass
            return 0.0
        # 정상 시작 — caller (`run` 메서드) 가 ``est`` 만큼 sleep 후 다음 step 으로 진행.
        # step 종료 시 별도 cleanup 을 하지 않으므로 freeze 가 유지되어 마지막 vt 시점의
        # 자세가 viewport 에 그대로 남는다.
        return est

    def _estimate_usd_timeline_duration(
        self,
        instance,
        *,
        range_mode: str,
        range_start: float,
        range_end: float,
        combined_speed: float,
    ) -> float:
        tps = LAM_FIXED_FPS
        if range_mode == "frames":
            length_sec = max(0.0, (range_end - range_start) / tps)
        elif range_mode == "ratio":
            full_len = max(0.0, instance.asset_end_time - instance.asset_start_time) / tps
            length_sec = max(0.0, full_len * max(0.0, min(1.0, range_end - range_start)))
        else:
            length_sec = max(0.0, (instance.asset_end_time - instance.asset_start_time) / tps)
        return length_sec / float(max(0.01, combined_speed))

    # ----------------------------------------------------------------- MOVE
    # ``move_from_initial=True``: (dx,dy,dz) = TBS_OFFSET **절대 목표** (기준 0 = 905.92mm).
    #   자동 Z: dz=25.928 [TBS/mm] — ``lam_slot_z_config`` + ``build_steps_for_event``.
    # ``move_from_initial=False``: 현재 위치에서 (dx,dy,dz) 만큼 **델타** 이동.
    # 실제 USD write: ``lam_translate_animation`` (main thread).

    def _start_move(self, idx: int, step: dict, speed_scale: float) -> float:
        from . import lam_translate_animation as _ltx

        prim_id = str(step.get("prim") or "")
        sp = float(max(0.01, speed_scale or 1.0))
        duration = float(step.get("duration", 1.0) or 1.0) / sp
        dx = float(step.get("dx", 0.0) or 0.0)
        dy = float(step.get("dy", 0.0) or 0.0)
        dz = float(step.get("dz", 0.0) or 0.0)
        from_initial = bool(step.get("move_from_initial", False))
        _seq_log(
            f"{_PRINT_PREFIX} _start_move idx={idx} prim_id={prim_id!r} "
            f"input=({dx},{dy},{dz}) from_initial={from_initial} dur={duration}",
            flush=True,
        )
        stage = _stage()
        paths = _resolve_prim_paths(stage, prim_id)
        _seq_log(f"{_PRINT_PREFIX} _start_move idx={idx} resolved_paths={paths}", flush=True)
        if not paths or duration <= 0:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] MOVE skip — prim={prim_id!r} paths={paths} dur={duration}",
                flush=True,
            )
            return 0.0

        if not from_initial and abs(dx) < 1e-9 and abs(dy) < 1e-9 and abs(dz) < 1e-9:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] MOVE skip — zero delta (move_from_initial=False)",
                flush=True,
            )
            return 0.0

        # USD write 는 반드시 main thread 에서 (lam_sequence_engine 상단 _dispatch_main 주석 참조).
        def _do_in_main() -> None:
            ctx_nm = self._usd_context_name
            prev = _push_lam_stage_context(ctx_nm)
            try:
                for p in paths:
                    try:
                        _ltx.stop_prim_translate_animation(p, ctx_nm)
                        if from_initial:
                            cur = _ltx.read_tbs_offset_translate_xyz(
                                p, usd_context_name=ctx_nm
                            )
                            ddx = float(dx) - float(cur[0])
                            ddy = float(dy) - float(cur[1])
                            ddz = float(dz) - float(cur[2])
                            if abs(ddx) < 1e-9 and abs(ddy) < 1e-9 and abs(ddz) < 1e-9:
                                _seq_log(
                                    f"{_PRINT_PREFIX} (main) step[{idx}] MOVE(initial) skip path={p!r} "
                                    f"already at target ({dx},{dy},{dz})",
                                    flush=True,
                                )
                                continue
                            seg_delta = (ddx, ddy, ddz)
                        else:
                            seg_delta = (dx, dy, dz)
                        _ltx.run_prim_translate_animation(
                            p,
                            [{"duration": duration, "delta": seg_delta}],
                            loop=False,
                            speed_ref=sp,
                            usd_context_name=ctx_nm,
                        )
                        if from_initial:
                            _seq_log(
                                f"{_PRINT_PREFIX} (main) MOVE(initial) prim={p} "
                                f"target=({dx},{dy},{dz}) delta={seg_delta} dur={duration}",
                                flush=True,
                            )
                        else:
                            _seq_log(
                                f"{_PRINT_PREFIX} (main) MOVE prim={p} d={seg_delta} dur={duration}",
                                flush=True,
                            )
                    except Exception as exc:
                        _seq_log(f"{_PRINT_PREFIX} (main) MOVE failed prim={p}: {exc}", flush=True)
            finally:
                _pop_lam_stage_context(prev)

        _seq_log(f"{_PRINT_PREFIX} _start_move idx={idx} dispatching to main thread", flush=True)
        _dispatch_main(_do_in_main)
        _seq_log(
            f"{_PRINT_PREFIX} step[{idx}] MOVE dispatched prim={paths} "
            f"from_initial={from_initial} dur={duration}",
            flush=True,
        )
        return duration

    # --------------------------------------------------------------- ROTATE

    def _start_rotate(self, idx: int, step: dict, speed_scale: float) -> float:
        from . import lam_rotate_animation as _lrx

        prim_id = str(step.get("prim") or "")
        sp = float(max(0.01, speed_scale or 1.0))
        duration = float(step.get("duration", 1.0) or 1.0) / sp
        rx = float(step.get("rx", 0.0) or 0.0)
        ry = float(step.get("ry", 0.0) or 0.0)
        rz = float(step.get("rz", 0.0) or 0.0)
        # 2026-05-12: 월드 피봇 회전 / lock_world_center 옵션 제거 — 옛 JSON 의
        # auto_pivot_world_center / user_axis_rotate / pivot_w* 는 모두 무시한다.
        from_initial = bool(step.get("rotate_from_initial", False))
        stage = _stage()
        paths = _resolve_prim_paths(stage, prim_id)
        if not paths or duration <= 0:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] ROTATE skip — prim={prim_id!r} paths={paths} dur={duration}",
                flush=True,
            )
            return 0.0

        # 모든 USD read/write 는 main thread 에서 (background 에서 USD read/op 생성 시
        # main 의 update/렌더 경로와 동시 접근으로 freeze 가능 — 2026-05-12 회귀 fix).
        from . import lam_translate_animation as _ltx

        # rotate_from_initial=False 인 경우는 background 에서 0 입력 skip 만 처리.
        if not from_initial:
            if abs(rx) < 1e-9 and abs(ry) < 1e-9 and abs(rz) < 1e-9:
                return 0.0

        def _do_in_main() -> None:
            ctx_nm = self._usd_context_name
            prev = _push_lam_stage_context(ctx_nm)
            try:
                # 1) 충돌 방지: 진행 중인 translate / rotate 모두 stop.
                for p in paths:
                    try:
                        _ltx.stop_prim_translate_animation(p, ctx_nm)
                        _lrx.stop_prim_rotate_animation(p, ctx_nm)
                    except Exception:
                        pass

                # 2) rotate_from_initial=True 인 스텝의 입력값 (rx,ry,rz) 은
                #    "USD 로드 시점 자산 원본 자세 = TBS_OFFSET 가 author 되지 않은 자세
                #    = (0,0,0) 기준 **절대 목표각**" 으로 해석한다.
                #
                #      target       = (rx, ry, rz)                       (절대)
                #      delta_to_run = wrap180(target - current_TBS_OFFSET) (최단 호)
                #
                #    LAM 에서는 자산 원본에 TBS_OFFSET RotateXYZ 가 author 되어 있지 않으므로
                #    baseline 은 항상 (0,0,0) 으로 고정. Run 을 여러 번 눌러도, 동일 prim 에
                #    대해 같은 입력 90° 를 반복해도 target 은 항상 동일한 90° 절대각으로 해석되어
                #    누적되지 않는다.
                per_prim_payload: Dict[str, tuple[float, float, float]] = {}
                if from_initial:
                    for p in paths:
                        cur = _lrx.read_tbs_offset_rotate_xyz_deg(
                            p, usd_context_name=ctx_nm
                        )
                        drx = _wrap_to_180(float(rx) - float(cur[0]))
                        dry = _wrap_to_180(float(ry) - float(cur[1]))
                        drz = _wrap_to_180(float(rz) - float(cur[2]))
                        per_prim_payload[p] = (drx, dry, drz)
                    if all(
                        abs(d[0]) < 1e-9 and abs(d[1]) < 1e-9 and abs(d[2]) < 1e-9
                        for d in per_prim_payload.values()
                    ):
                        _seq_log(
                            f"{_PRINT_PREFIX} (main) step[{idx}] ROTATE(initial) skip — "
                            f"already at target (input={rx},{ry},{rz})",
                            flush=True,
                        )
                        return
                else:
                    for p in paths:
                        per_prim_payload[p] = (rx, ry, rz)

                # 3) simple (= 유일한 모드)
                for p, (drx, dry, drz) in per_prim_payload.items():
                    if abs(drx) < 1e-9 and abs(dry) < 1e-9 and abs(drz) < 1e-9:
                        continue
                    _lrx.run_prim_rotate_animation(
                        p,
                        [{"duration": duration, "delta": (drx, dry, drz)}],
                        loop=False,
                        speed_ref=sp,
                        usd_context_name=ctx_nm,
                    )
                if from_initial:
                    _seq_log(
                        f"{_PRINT_PREFIX} (main) ROTATE simple(from_initial) prim={paths} "
                        f"input=({rx},{ry},{rz}) per_prim_delta={per_prim_payload} dur={duration}",
                        flush=True,
                    )
                else:
                    _seq_log(
                        f"{_PRINT_PREFIX} (main) ROTATE simple prim={paths} "
                        f"r=({rx},{ry},{rz}) dur={duration}",
                        flush=True,
                    )
            except Exception as exc:
                _seq_log(f"{_PRINT_PREFIX} (main) ROTATE failed: {exc}", flush=True)
            finally:
                _pop_lam_stage_context(prev)

        _seq_log(
            f"{_PRINT_PREFIX} _start_rotate idx={idx} dispatching to main thread "
            f"prim={paths} r=({rx},{ry},{rz}) from_initial={from_initial} dur={duration}",
            flush=True,
        )
        _dispatch_main(_do_in_main)
        return duration

    # -------------------------------------------------------- SET_PRIM_VISIBILITY

    def _start_set_prim_visibility(self, idx: int, step: dict, speed_scale: float) -> float:
        """Imageable prim visibility 를 즉시 설정(sticky). ``duration`` 은 그룹 대기용 tail."""
        from pxr import UsdGeom  # type: ignore

        prim_id = str(step.get("prim") or "")
        visible = prim_visibility_step_visible(step)
        sp = float(max(0.01, speed_scale or 1.0))
        tail = float(step.get("duration", 0.02) or 0.02) / sp
        stage = _stage()
        paths = _resolve_prim_paths(stage, prim_id)
        if not paths:
            _seq_log(
                f"{_PRINT_PREFIX} step[{idx}] SET_PRIM_VISIBILITY skip — prim={prim_id!r}",
                flush=True,
            )
            return max(0.0, tail)

        def _do_in_main() -> None:
            ctx_nm = self._usd_context_name
            prev = _push_lam_stage_context(ctx_nm)
            try:
                from .lam_usd_stage_context import get_stage_for_context_name

                st = (
                    get_stage_for_context_name(ctx_nm)
                    if ctx_nm
                    else _stage()
                )
                if st is None:
                    return
                for p in paths:
                    try:
                        prim = st.GetPrimAtPath(p)
                        if not prim or not prim.IsValid():
                            continue
                        img = UsdGeom.Imageable(prim)
                        if not img:
                            continue
                        if visible:
                            img.MakeVisible()
                        else:
                            img.MakeInvisible()
                    except Exception as exc:
                        _seq_log(
                            f"{_PRINT_PREFIX} (main) SET_PRIM_VISIBILITY failed path={p}: {exc}",
                            flush=True,
                        )
            finally:
                _pop_lam_stage_context(prev)

        _dispatch_main_wait(_do_in_main, timeout=5.0)
        label_ctx = step.get("_lam_wafer_label_ctx")
        if label_ctx:
            try:
                from .lam_wafer_viewport_labels import (
                    get_wafer_label_tracker,
                    wafer_label_tracking_enabled,
                )

                if wafer_label_tracking_enabled():
                    si = int(getattr(self, "_play_screen", 1) or 1)
                    tracker = get_wafer_label_tracker(si)
                    for p in paths:
                        tracker.on_visibility(p, visible, label_ctx, screen=si)
            except Exception:
                pass
        _seq_log(
            f"{_PRINT_PREFIX} step[{idx}] SET_PRIM_VISIBILITY paths={paths} visible={visible} tail={tail:.3f}s",
            flush=True,
        )
        return max(0.0, tail)

    # --------------------------------------------------------------- (initial-rotate cache removed)
    #
    # 2026-05-12 후반: `rotate_from_initial=True` 의 baseline 을 "처음 진입 시점의 현재값"
    # 으로 캐싱하던 방식은 사용자 의도와 불일치했다(Run 을 다시 누르거나 새 runner 가 만들어지면
    # 직전 자세가 baseline 이 되어 같은 입력 90° 가 누적 회전되는 회귀). LAM 에서 자산 원본은
    # TBS_OFFSET 을 author 하지 않으므로 baseline 은 **항상 (0,0,0)** 으로 고정한다. 이 경우
    # `target = (rx, ry, rz)` (절대 각도), `delta = wrap180(target - current)` 로 단순화되어
    # runner 인스턴스/Run 횟수와 무관하게 동일하게 동작한다.

    # ------------------------------------------------------------ wait/sleep

    def _wait_for(self, target_monotonic: float) -> None:
        while not self._stop_flag.is_set():
            now = time.monotonic()
            if now >= target_monotonic:
                return
            try:
                from .simulation_play import csv_play_session_active

                if csv_play_session_active():
                    _csv_play_nominal_sleep(
                        self,
                        target_monotonic - now,
                        allow_stop=True,
                    )
                    return
            except Exception:
                pass
            chunk = min(0.1, target_monotonic - now)
            time.sleep(max(0.0, chunk))

    def _sleep(self, sec: float, *, allow_stop: bool = True) -> None:
        if sec <= 0:
            return
        try:
            from .simulation_play import csv_play_session_active

            if csv_play_session_active():
                _csv_play_nominal_sleep(self, sec, allow_stop=allow_stop)
                return
        except Exception:
            pass
        end = time.monotonic() + sec
        while True:
            if allow_stop and self._stop_flag.is_set():
                return
            now = time.monotonic()
            if now >= end:
                return
            time.sleep(min(0.1, end - now))

    # ---------------------------------------------------------- hide helpers

    def _schedule_unhide_after(self, paths: List[str], delay_sec: float) -> None:
        """step 의 estimated duration 이 끝난 뒤 hide refcount 감소(0 이면 visible)."""
        # 별도 timer thread 로 dispatch (메인 스레드 freeze 안 시키도록).
        def _later() -> None:
            self._sleep(max(0.0, float(delay_sec)) + 0.05, allow_stop=False)
            try:
                self._hide.schedule_unhide(paths, delay_sec=0.0)
            except Exception:
                pass

        threading.Thread(target=_later, name="lam_seq_unhide_dispatch", daemon=True).start()

    # --------------------------------------------------- start_snapshot 메타

    @staticmethod
    def _parse_start_snapshot(raw: Any) -> Dict[str, Dict[str, Any]]:
        """TBS 와 동일 schema. (m16, t/r) 를 안전 변환."""
        from pxr import Gf  # type: ignore

        out: Dict[str, Dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return out
        for path, rec in raw.items():
            if not isinstance(path, str) or not isinstance(rec, dict):
                continue
            t = rec.get("t")
            r = rec.get("r")
            if not (
                isinstance(t, (list, tuple))
                and isinstance(r, (list, tuple))
                and len(t) >= 3
                and len(r) >= 3
            ):
                continue
            try:
                mode_raw = str(rec.get("mode") or "").strip()
                mode = "composed_local" if mode_raw == "composed_local" else "offset_only"
                entry: Dict[str, Any] = {
                    "t": Gf.Vec3f(float(t[0]), float(t[1]), float(t[2])),
                    "r": Gf.Vec3f(float(r[0]), float(r[1]), float(r[2])),
                    "mode": mode,
                }
                m16 = rec.get("m16")
                if isinstance(m16, (list, tuple)) and len(m16) >= 16:
                    entry["m16"] = [float(m16[i]) for i in range(16)]
                out[path] = entry
            except Exception:
                continue
        return out

    @staticmethod
    def _apply_start_snapshot(snapshot: Dict[str, Dict[str, Any]]) -> None:
        """parent-relative 로컬 매트릭스(m16) 를 TBS_OFFSET 두 op (Translate+RotateXYZ) 로 분해 author."""
        from . import lam_translate_animation as _ltx
        from . import lam_rotate_animation as _lrx
        from pxr import Gf  # type: ignore

        stage = _stage()
        if stage is None:
            return
        for path, rec in snapshot.items():
            try:
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                t = rec.get("t")
                r = rec.get("r")
                mode = str(rec.get("mode") or "offset_only")
                if mode == "composed_local" and rec.get("m16"):
                    m16 = rec.get("m16")
                    M = Gf.Matrix4d()
                    for i in range(4):
                        for j in range(4):
                            M[i][j] = float(m16[i * 4 + j])
                    tr = M.ExtractTranslation()
                    rot = M.ExtractRotation()
                    rxyz = rot.Decompose(
                        Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1)
                    )
                    _ltx._set_prim_translate(
                        prim, Gf.Vec3f(float(tr[0]), float(tr[1]), float(tr[2]))
                    )
                    _lrx._set_prim_rotate_xyz(
                        prim,
                        Gf.Vec3f(float(rxyz[0]), float(rxyz[1]), float(rxyz[2])),
                    )
                    continue
                if t is not None:
                    _ltx._set_prim_translate(prim, t)
                if r is not None:
                    _lrx._set_prim_rotate_xyz(prim, r)
            except Exception as exc:
                _seq_log(f"{_PRINT_PREFIX} _apply_start_snapshot path={path}: {exc}", flush=True)


__all__ = ["LamSequenceRunner"]

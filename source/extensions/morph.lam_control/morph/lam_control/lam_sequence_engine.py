"""LAM 시퀀스 엔진.

step 종류: USD_TIMELINE / TIMESAMPLES_REPLAY / MOVE / ROTATE / DELAY.

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
- MOVE/ROTATE step 은 LAM 측 animator 호출(=update tick subscription) 후 duration 만큼
  sleep. animator 자체는 main-thread update 에서 보간 진행.
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


# step kind 상수 — 추후 dispatch / UI 가 공통으로 참조.
STEP_KIND_USD_TIMELINE: str = "USD_TIMELINE"
STEP_KIND_TIMESAMPLES_REPLAY: str = "TIMESAMPLES_REPLAY"
STEP_KIND_MOVE: str = "MOVE"
STEP_KIND_ROTATE: str = "ROTATE"
STEP_KIND_DELAY: str = "DELAY"

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
                    print(f"{_PRINT_PREFIX} prim_path not in stage: {s}", flush=True)
            except Exception:
                print(f"{_PRINT_PREFIX} prim_path resolve failed: {s}", flush=True)
        else:
            print(f"{_PRINT_PREFIX} only absolute /World/... paths supported, got: {s}", flush=True)
    return out


def _stage():
    try:
        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


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
            print(f"{_PRINT_PREFIX} dispatch_main fn failed: {exc}", flush=True)
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
            _do, name="morph.lam_control.sequence_engine.dispatch_main"
        )
    except Exception as exc:
        # fallback — Kit 가 없는 환경/테스트. 그냥 직접 호출 (이 환경에선 deadlock 도 없을 것).
        print(f"{_PRINT_PREFIX} dispatch_main fallback (direct call): {exc}", flush=True)
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
        print(f"{_PRINT_PREFIX} _dispatch_main_wait TIMEOUT after {timeout}s", flush=True)
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
        print(f"{_PRINT_PREFIX} fallback timeSamples scan failed prim={prim_path}: {exc}", flush=True)

    if mn is not None and mx is not None and mx > mn:
        # FPS 30 고정 정책 — tps 는 항상 LAM_FIXED_FPS.
        tps = LAM_FIXED_FPS
        try:
            instance.asset_start_time = float(mn)
            instance.asset_end_time = float(mx)
            instance.asset_tps = float(tps)
        except Exception:
            pass
        print(
            f"{_PRINT_PREFIX} fallback asset timeline filled prim={prim_path} "
            f"timeSamples [{mn},{mx}]@{tps}fps(forced)  (n_attr_with_samples={n_attr})",
            flush=True,
        )
        return (float(mn), float(mx), float(tps))

    print(
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


def _reset_tbs_offset_ops_for_paths(paths: List[str]) -> None:
    """애니메이션 중지 후 `TBS_OFFSET` Translate/Rotate 를 0 으로."""
    from . import lam_translate_animation as _ltx
    from . import lam_rotate_animation as _lrx

    try:
        _lrx.stop_world_pivot_rotate_animation()
    except Exception:
        pass
    for p in paths:
        try:
            _ltx.stop_prim_translate_animation(p)
            _lrx.stop_prim_rotate_animation(p)
        except Exception:
            pass
        try:
            _ltx.zero_tbs_offset_translate_at_path(p)
            _lrx.zero_tbs_offset_rotate_at_path(p)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} zero TBS_OFFSET failed path={p}: {exc}", flush=True)


# --------------------------------------------------------------------- runner

class LamSequenceRunner:
    """1 시퀀스의 step 배열을 순차 실행하는 러너.

    background thread 에서 동기 호출 권장. main thread 에서 호출하면 UI 가 freeze 한다.
    """

    def __init__(
        self,
        registry: AnimationInstanceRegistry,
        scheduler: PlaybackScheduler,
        on_step_resolved: Optional[Callable[[int, dict, ResolveResult], None]] = None,
    ) -> None:
        self._registry = registry
        self._scheduler = scheduler
        self._on_step_resolved = on_step_resolved
        self._stop_flag = threading.Event()
        self._hide = LamHideController()
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
                `stop_all_*` 는 호출하지 않는다. `lam_event_playlist_window` 가 동일
                prim / 인스턴스 충돌 시 선점(preempt) 할 때 사용한다(다른 JSON 의
                MOVE/ROTATE 를 건드리지 않기 위함).
        """
        self._stop_flag.set()
        if cancel_all_move_rotate:
            try:
                from . import lam_translate_animation as _ltx
                from . import lam_rotate_animation as _lrx

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
    ) -> None:
        """동기 차단형 시퀀스 실행. background thread 에서 호출 권장."""
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

        # Run(reset): 시퀀스에 나오는 prim 들의 TBS_OFFSET 를 0 으로 되돌린 뒤 재생.
        # (그렇지 않으면 MOVE 가 현재 누적 오프셋을 시작점으로 삼아 "현재 위치에서만" 움직임.)
        if reset_each_start:
            rpaths = _collect_prim_paths_for_reset(steps)
            print(
                f"{_PRINT_PREFIX} reset_each_start: zero TBS_OFFSET for {len(rpaths)} prim(s)",
                flush=True,
            )
            try:
                _dispatch_main_wait(lambda: _reset_tbs_offset_ops_for_paths(rpaths), timeout=15.0)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} reset TBS_OFFSET failed: {exc}", flush=True)

        # 첫 step 메타 (TBS 와 동일 schema). LAM 의 baseline 모델 차이 — _start_from_current
        # 자체는 LAM 에서 거의 default 동작이지만 (LAM 은 baseline 강제 복원이 없음),
        # _start_snapshot 만 의미가 살아 있다 → 시작 직전에 prim 별 m16 을 TBS_OFFSET 두 op
        # 로 분해 author 한다.
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
                print(f"{_PRINT_PREFIX} _apply_start_snapshot failed: {exc}", flush=True)

        # 첫 step 의 step_delay_ms 는 시퀀스 시작 전 초기 대기.
        d0_ms = int(first.get("step_delay_ms", 0) or 0)
        d0 = max(0.0, (d0_ms / 1000.0) / sp)
        if d0 > 0:
            self._sleep(d0)

        a = 0
        while a < len(steps):
            if self._stop_flag.is_set():
                print(f"{_PRINT_PREFIX} stop requested at step[{a}]", flush=True)
                break
            b = _group_end_index(steps, a)
            self._execute_group(steps, a, b, sp, reset_each_start)
            next_idx = b + 1
            if next_idx < len(steps):
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

        anchor (b) 의 estimated duration 까지 wait 한다. follower 자기 duration 은
        anchor 와 무관하게 background 에서 진행되므로, 본 함수는 anchor 종료만 보장.
        """
        if self._stop_flag.is_set():
            return
        leader_idx = a
        anchor_idx = b
        t_group_start = time.monotonic()

        # leader 즉시 시작.
        leader_dur = self._start_step(leader_idx, steps[leader_idx], speed_scale, reset_each_start)
        if leader_idx == anchor_idx:
            # 단일 step 그룹.
            self._wait_for(t_group_start + leader_dur)
            return

        # follower 들 dispatch (각자 별도 thread 에서 step_delay_ms 만큼 sleep 후 start).
        follower_threads: List[threading.Thread] = []
        # anchor 종료 시점 추적용.
        anchor_finish_at_holder: Dict[str, float] = {"t": t_group_start + leader_dur}

        for i in range(a + 1, b + 1):
            step_i = steps[i] or {}
            delay_sec = max(0.0, (int(step_i.get("step_delay_ms", 0) or 0) / 1000.0) / speed_scale)

            def _runner_for(idx: int = i, step: dict = step_i, delay: float = delay_sec) -> None:
                if delay > 0:
                    self._sleep(delay, allow_stop=True)
                if self._stop_flag.is_set():
                    return
                start_at = time.monotonic()
                dur = self._start_step(idx, step, speed_scale, reset_each_start)
                if idx == anchor_idx:
                    anchor_finish_at_holder["t"] = start_at + dur

            t = threading.Thread(target=_runner_for, name=f"lam_seq_follower_{i}", daemon=True)
            t.start()
            follower_threads.append(t)

        # anchor 가 leader 가 아닌 경우 — anchor follower thread 가 anchor_finish_at_holder["t"]
        # 를 갱신할 때까지 잠깐 wait 해야 한다. anchor follower 는 자기 step_delay_ms 만큼
        # 처음 sleep 하므로 그만큼은 최소 wait.
        # 가장 보수적: 모든 follower thread 가 _start_step 호출까지 진행될 시간(=각 follower 의
        # step_delay_ms 의 max + 100ms 버퍼) 동안 wait 후 anchor_finish_at_holder["t"] 사용.
        anchor_step = steps[anchor_idx] or {}
        anchor_delay_sec = max(
            0.0, (int(anchor_step.get("step_delay_ms", 0) or 0) / 1000.0) / speed_scale
        )
        # anchor follower 가 _start_step 호출을 끝낼 추정 시각 + 약간의 마진.
        self._sleep(anchor_delay_sec + 0.05, allow_stop=True)
        # 이 시점에 anchor_finish_at_holder["t"] 가 anchor 의 종료 시각으로 갱신되어 있을 것.
        self._wait_for(anchor_finish_at_holder["t"])

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
            else:
                print(f"{_PRINT_PREFIX} step[{idx}] unknown type={t!r}", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} step[{idx}] {t} failed: {exc}", flush=True)
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
            print(
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
            print(
                f"{_PRINT_PREFIX} step[{idx}] {step_kind_label} inst prim={inst_dbg.prim_path} "
                f"asset_time=[{inst_dbg.asset_start_time},{inst_dbg.asset_end_time}]"
                f"@{inst_dbg.asset_tps}fps "
                f"current_state={inst_dbg.state} virtual_time={inst_dbg.virtual_time:.3f}s "
                f"speed={inst_dbg.speed} loop={inst_dbg.loop}",
                flush=True,
            )
        except Exception as _exc:
            print(f"{_PRINT_PREFIX} step[{idx}] inst dbg print failed: {_exc}", flush=True)

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
            print(f"{_PRINT_PREFIX} step[{idx}] asset timeline fallback failed: {exc}", flush=True)

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
                print(f"{_PRINT_PREFIX} step[{idx}] offset_correction failed: {exc}", flush=True)

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
                print(
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
                print(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE master-timeline "
                    f"prim={prim_path} frames=[{play_sf},{play_ef}] "
                    f"begin_ok={ok_b} play_ok={ok_p}",
                    flush=True,
                )

            try:
                _dispatch_main_wait(_tl_begin, timeout=15.0)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE begin failed: {exc}",
                    flush=True,
                )
                return 0.0

            self._sleep(est, allow_stop=True)

            # 사용자 요청 (2026-05-12) — USD_TIMELINE step 이 끝나면 viewport 가 끝
            # 프레임에 머무르도록 한다. 즉:
            #   (1) omni.timeline 을 끝 시간 (= play_ef/fps) 에서 pause + 그 위치 유지
            #       (saved_time 으로 0 초 회귀 X).
            #   (2) Option E freeze 를 LayerOffset(play_ef, 1e-9) 로 author —
            #       이후 timeline 이 슬라이더로 0 으로 돌아가더라도 reference 가
            #       끝 프레임 시점의 값을 평가해 viewport 가 그 자세로 멈춘다.
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
                print(
                    f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE end failed: {exc}",
                    flush=True,
                )

            print(
                f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE matched_by={result.matched_by} "
                f"prim={prim_path} range=frames[{play_sf},{play_ef}] "
                f"sp={combined_speed} loop={loop} mode=MASTER_TIMELINE est_duration={est:.3f}s "
                f"asset_time=[{result.instance.asset_start_time},{result.instance.asset_end_time}]"
                f"@{LAM_FIXED_FPS}fps(forced)",
                flush=True,
            )
            return 0.0

        if step_kind_label == STEP_KIND_USD_TIMELINE and loop:
            print(
                f"{_PRINT_PREFIX} step[{idx}] USD_TIMELINE loop=True — Option E 로 폴백 "
                f"(master omni.timeline 루프는 미구현)",
                flush=True,
            )

        # ------------------------------------------------------------------ TIMESAMPLES_REPLAY (또는 USD_TIMELINE+loop) — Option E
        # scheduler.start 도 main thread 에서 실행 (USD attribute 평가 lock 보호).
        start_ok_holder: Dict[str, bool] = {"ok": False}

        def _do_start_in_main() -> None:
            start_ok_holder["ok"] = bool(
                self._scheduler.start(
                    result.instance.prim_path,
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
            print(f"{_PRINT_PREFIX} step[{idx}] scheduler.start failed: {exc}", flush=True)
        ok = start_ok_holder["ok"]

        print(
            f"{_PRINT_PREFIX} step[{idx}] {step_kind_label} matched_by={result.matched_by} "
            f"prim={result.instance.prim_path} range={range_mode}[{range_start},{range_end}] "
            f"sp={combined_speed} loop={loop} ok={ok} est_duration={est:.3f}s "
            f"asset_time=[{result.instance.asset_start_time},{result.instance.asset_end_time}]"
            f"@{LAM_FIXED_FPS}fps(forced)",
            flush=True,
        )
        if not ok or loop:
            return 0.0
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

    def _start_move(self, idx: int, step: dict, speed_scale: float) -> float:
        from . import lam_translate_animation as _ltx

        prim_id = str(step.get("prim") or "")
        sp = float(max(0.01, speed_scale or 1.0))
        duration = float(step.get("duration", 1.0) or 1.0) / sp
        dx = float(step.get("dx", 0.0) or 0.0)
        dy = float(step.get("dy", 0.0) or 0.0)
        dz = float(step.get("dz", 0.0) or 0.0)
        print(
            f"{_PRINT_PREFIX} _start_move idx={idx} prim_id={prim_id!r} d=({dx},{dy},{dz}) dur={duration}",
            flush=True,
        )
        stage = _stage()
        paths = _resolve_prim_paths(stage, prim_id)
        print(f"{_PRINT_PREFIX} _start_move idx={idx} resolved_paths={paths}", flush=True)
        if not paths or duration <= 0:
            print(
                f"{_PRINT_PREFIX} step[{idx}] MOVE skip — prim={prim_id!r} paths={paths} dur={duration}",
                flush=True,
            )
            return 0.0

        # USD write 는 반드시 main thread 에서 (lam_sequence_engine 상단 _dispatch_main 주석 참조).
        def _do_in_main() -> None:
            for p in paths:
                try:
                    _ltx.stop_prim_translate_animation(p)
                    _ltx.run_prim_translate_animation(
                        p,
                        [{"duration": duration, "delta": (dx, dy, dz)}],
                        loop=False,
                    )
                    print(f"{_PRINT_PREFIX} (main) MOVE started prim={p}", flush=True)
                except Exception as exc:
                    print(f"{_PRINT_PREFIX} (main) MOVE failed prim={p}: {exc}", flush=True)

        print(f"{_PRINT_PREFIX} _start_move idx={idx} dispatching to main thread", flush=True)
        _dispatch_main(_do_in_main)
        print(
            f"{_PRINT_PREFIX} step[{idx}] MOVE dispatched prim={paths} d=({dx},{dy},{dz}) dur={duration}",
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
            print(
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
            try:
                # 1) 충돌 방지: 진행 중인 translate / rotate 모두 stop.
                for p in paths:
                    try:
                        _ltx.stop_prim_translate_animation(p)
                        _lrx.stop_prim_rotate_animation(p)
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
                        cur = _lrx.read_tbs_offset_rotate_xyz_deg(p)
                        drx = _wrap_to_180(float(rx) - float(cur[0]))
                        dry = _wrap_to_180(float(ry) - float(cur[1]))
                        drz = _wrap_to_180(float(rz) - float(cur[2]))
                        per_prim_payload[p] = (drx, dry, drz)
                    if all(
                        abs(d[0]) < 1e-9 and abs(d[1]) < 1e-9 and abs(d[2]) < 1e-9
                        for d in per_prim_payload.values()
                    ):
                        print(
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
                    )
                if from_initial:
                    print(
                        f"{_PRINT_PREFIX} (main) ROTATE simple(from_initial) prim={paths} "
                        f"input=({rx},{ry},{rz}) per_prim_delta={per_prim_payload} dur={duration}",
                        flush=True,
                    )
                else:
                    print(
                        f"{_PRINT_PREFIX} (main) ROTATE simple prim={paths} "
                        f"r=({rx},{ry},{rz}) dur={duration}",
                        flush=True,
                    )
            except Exception as exc:
                print(f"{_PRINT_PREFIX} (main) ROTATE failed: {exc}", flush=True)

        print(
            f"{_PRINT_PREFIX} _start_rotate idx={idx} dispatching to main thread "
            f"prim={paths} r=({rx},{ry},{rz}) from_initial={from_initial} dur={duration}",
            flush=True,
        )
        _dispatch_main(_do_in_main)
        return duration

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
            chunk = min(0.1, target_monotonic - now)
            time.sleep(max(0.0, chunk))

    def _sleep(self, sec: float, *, allow_stop: bool = True) -> None:
        if sec <= 0:
            return
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
                print(f"{_PRINT_PREFIX} _apply_start_snapshot path={path}: {exc}", flush=True)


__all__ = ["LamSequenceRunner"]

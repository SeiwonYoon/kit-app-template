from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SimTimelineItem:
    """프리런 결과의 단일 아이템(시뮬 시간 기준)."""

    t: float
    kind: str  # "log" | "event" | "progress"
    payload: Any


@dataclass(frozen=True)
class SimPreRunResult:
    """프리런 결과(화면 1개)."""

    screen: int
    final_sim_time: float
    total_est_sec: float
    items: Tuple[SimTimelineItem, ...]


@dataclass
class EpBarPrecomputed:
    """프리런 progress 시계열로 미리 계산한 EP 막대 rows."""

    total_est: float
    rows: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    ep_ports: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TimetableRowMeta:
    """타임테이블 UI 한 줄(클릭 단위) 메타."""

    row_index: int
    t: float
    kind: str  # "event" | "step"
    json_obj: Dict[str, Any]
    display_line: str
    through_item_index: int  # Fast-apply 시 items[0..through_item_index] 포함


def _f_val(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).strip() or default)
    except Exception:
        return float(default)


def _s_val(v: Any) -> str:
    try:
        return str(v).strip() if v is not None else ""
    except Exception:
        return ""


def _format_timetable_proc_line_ko(proc_sec: float, anim_sec: float, *, process_time_priority: bool) -> str:
    p = max(0.0, float(proc_sec))
    a = max(0.0, float(anim_sec))
    if process_time_priority:
        return f"공정시간 우선: {p:.1f}s (공정 {p:.1f}s)"
    if a > 1e-9:
        return f"공정시간: {max(p, a):.1f}s (max(공정 {p:.1f}s, 애니 {a:.1f}s))"
    return f"공정시간: {p:.1f}s"


def _format_timetable_anim_line_ko(anim: str, anim_sec: float) -> str:
    name = _s_val(anim)
    if not name:
        return "애니메이션: 없음"
    bn = name.replace("\\", "/").rsplit("/", 1)[-1]
    a = max(0.0, float(anim_sec))
    if a > 1e-9:
        return f"애니메이션: {bn} (추정 {a:.1f}s)"
    return f"애니메이션: {bn}"


def format_timetable_display_line(row: Dict[str, Any]) -> str:
    """
    타임테이블 UI 한 줄.

    - 앞부분: ``t``·``screen``·``event`` 등 핵심 필드만 담은 짧은 JSON (kind/detail/proc_sec/anim_sec 키 제외)
    - 뒷부분: 엔진 로그와 동일 톤의 ``공정시간: …`` · ``애니메이션: …`` 한글 문구
    """
    kind = _s_val(row.get("kind")).lower()
    proc_sec = _f_val(row.get("proc_sec", 0.0), 0.0)
    anim_sec = _f_val(row.get("anim_sec", 0.0), 0.0)
    anim_file = _s_val(row.get("anim"))
    ptp = _s_val(row.get("process_time_priority")).lower() in ("1", "true", "on", "yes")

    omit_keys = frozenset({"kind", "detail", "proc_sec", "anim_sec"})
    disp: Dict[str, Any] = {}
    for k, v in row.items():
        if k in omit_keys:
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if k == "anim":
            disp[k] = anim_file.replace("\\", "/").rsplit("/", 1)[-1] if anim_file else ""
            continue
        disp[k] = v

    parts: List[str] = []
    try:
        parts.append(json.dumps(disp, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        parts.append(str(disp))

    if kind == "step":
        parts.append(_format_timetable_proc_line_ko(proc_sec, anim_sec, process_time_priority=ptp))
        parts.append(_format_timetable_anim_line_ko(anim_file, anim_sec))

    return "  ".join(p for p in parts if str(p).strip())


def _push_bar_seg(segs: List[Dict[str, Any]], empty: bool, dur: float) -> None:
    if dur <= 1e-9:
        return
    if segs and isinstance(segs[-1], dict) and bool(segs[-1].get("empty")) == bool(empty):
        segs[-1]["dur"] = float(segs[-1].get("dur", 0.0)) + float(dur)
    else:
        segs.append({"empty": bool(empty), "dur": float(dur)})
    if len(segs) > 220:
        del segs[:-200]


def build_ep_bar_from_progress_items(
    items: Tuple[SimTimelineItem, ...],
    *,
    final_sim_time: float,
    ep_ports: Optional[List[str]] = None,
) -> EpBarPrecomputed:
    """
    프리런 ``progress`` payload의 ``ep_occ`` 를 시간순으로 누적해 완성 막대 rows 를 만든다.
    """
    eps: List[str] = []
    if ep_ports:
        eps = [str(x).strip().upper() for x in ep_ports if str(x).strip().upper().startswith("EP")]
    rows: Dict[str, List[Dict[str, Any]]] = {}
    t_last: Optional[float] = None
    total_est = max(0.0, float(final_sim_time))

    prog_items = [it for it in items if str(it.kind).lower() == "progress" and isinstance(it.payload, dict)]
    prog_items.sort(key=lambda it: float(it.t))

    for it in prog_items:
        p = dict(it.payload)
        t_now = _f_val(p.get("sim_time", it.t), float(it.t))
        if t_last is None:
            t_last = t_now
            continue
        dt = max(0.0, t_now - float(t_last))
        t_last = t_now
        if dt <= 1e-9:
            continue

        ep_occ = p.get("ep_occ", {})
        if not isinstance(ep_occ, dict):
            ep_occ = {}
        ep_list = list(eps)
        if not ep_list:
            ep_ports_raw = p.get("ep_ports", [])
            if isinstance(ep_ports_raw, list) and ep_ports_raw:
                ep_list = [str(x).strip().upper() for x in ep_ports_raw if str(x).strip().upper().startswith("EP")]
        if not ep_list:
            ep_list = sorted(
                [str(k).strip().upper() for k in ep_occ.keys() if str(k).strip().upper().startswith("EP")],
                key=lambda x: int(str(x).replace("EP", "") or "0"),
            )
        if not ep_list:
            ep_list = ["EP1", "EP2"]

        all_empty = str(p.get("all_ep_empty", "0")).strip() in ("1", "true", "True", "ON", "on")
        for ep in ep_list:
            if ep not in rows:
                rows[ep] = []
            v = str(ep_occ.get(ep, "EMPTY")).strip().upper()
            _push_bar_seg(rows[ep], empty=(v == "EMPTY"), dur=dt)
        if "ALL_EP" not in rows:
            rows["ALL_EP"] = []
        _push_bar_seg(rows["ALL_EP"], empty=bool(all_empty), dur=dt)
        eps = ep_list

    if not eps:
        eps = ["EP1", "EP2"]
    for r in list(eps) + ["ALL_EP"]:
        if r not in rows:
            rows[r] = []

    if total_est <= 0.0 and t_last is not None:
        total_est = max(30.0, float(t_last))

    return EpBarPrecomputed(total_est=float(total_est), rows=rows, ep_ports=tuple(eps))


def truncate_bar_rows_at_t(rows: Dict[str, List[Dict[str, Any]]], t_cut: float) -> Dict[str, List[Dict[str, Any]]]:
    """완성 rows 에서 ``t_cut`` 초까지만 잘라 복사본을 반환."""
    t_cut = max(0.0, float(t_cut))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row_name, segs in (rows or {}).items():
        acc = 0.0
        clipped: List[Dict[str, Any]] = []
        for seg in segs or []:
            if not isinstance(seg, dict):
                continue
            dur = float(seg.get("dur", 0.0))
            if acc + dur <= t_cut + 1e-9:
                clipped.append({"empty": bool(seg.get("empty")), "dur": dur})
                acc += dur
            elif acc < t_cut - 1e-9:
                rem = t_cut - acc
                clipped.append({"empty": bool(seg.get("empty")), "dur": rem})
                acc = t_cut
                break
            else:
                break
        out[str(row_name)] = clipped
    return out


def build_timetable_row_metas(res: SimPreRunResult) -> List[TimetableRowMeta]:
    """
    ``_build_prerun_timetable_text`` 와 동일 필터·정렬로 UI 행 메타를 만든다.
    각 행은 ``through_item_index`` 로 Fast-apply 범위를 지정한다.
    """
    si = int(res.screen)
    items = res.items
    item_by_key: Dict[Tuple[float, str, str], int] = {}
    for idx, it in enumerate(items):
        kind = str(it.kind or "").strip().lower()
        p = it.payload
        if kind == "event" and isinstance(p, dict):
            seq = _s_val(p.get("seq")).upper()
            if seq:
                item_by_key[(round(float(it.t), 4), "event", seq)] = idx
        elif kind == "progress" and isinstance(p, dict):
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed", 0.0), 0.0)
            if st == "RUNNING" and abs(el) <= 1e-9:
                ev = _s_val(p.get("event_seq") or p.get("sequence_name")).upper()
                if ev:
                    item_by_key[(round(float(it.t), 4), "step", ev)] = idx

    rows_data: List[Dict[str, Any]] = []
    for it in items:
        kind = str(it.kind or "").strip().lower()
        p = it.payload
        t_val = round(_f_val(it.t, 0.0), 2)
        if kind == "event" and isinstance(p, dict):
            seq = _s_val(p.get("seq")).upper()
            if not seq:
                continue
            row: Dict[str, Any] = {"t": t_val, "screen": si, "kind": "event", "event": seq}
            for k in ("port_id", "from_port_id", "to_port_id", "lot_id", "foup_id", "lot_seq"):
                v = _s_val(p.get(k))
                if v:
                    row[k] = v
            rows_data.append(row)
        elif kind == "progress" and isinstance(p, dict):
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed", 0.0), 0.0)
            if st != "RUNNING" or abs(el) > 1e-9:
                continue
            ev = _s_val(p.get("event_seq") or p.get("sequence_name")).upper()
            if not ev:
                continue
            row = {"t": t_val, "screen": si, "kind": "step", "event": ev}
            pid = _s_val(p.get("port_id"))
            if pid:
                row["port_id"] = pid
            label = _s_val(p.get("label"))
            if label:
                row["label"] = label
            row["anim"] = _s_val(p.get("linked_anim_json"))
            row["proc_sec"] = round(_f_val(p.get("proc_sec", 0.0), 0.0), 2)
            row["anim_sec"] = round(_f_val(p.get("anim_sec", 0.0), 0.0), 2)
            detail = _s_val(p.get("detail"))
            if detail:
                row["detail"] = detail
            ptp = _s_val(p.get("process_time_priority"))
            if ptp:
                row["process_time_priority"] = ptp
            rows_data.append(row)

    if not rows_data:
        return []

    kind_prio = {"event": 0, "step": 1}
    rows_data.sort(
        key=lambda r: (
            float(r.get("t", 0.0)),
            int(kind_prio.get(str(r.get("kind", "")), 9)),
        )
    )

    metas: List[TimetableRowMeta] = []
    for ri, r in enumerate(rows_data):
        t_val = float(r.get("t", 0.0))
        kind = str(r.get("kind", ""))
        ev = _s_val(r.get("event")).upper()
        key = (round(t_val, 4), kind, ev)
        through = int(item_by_key.get(key, -1))
        if through < 0:
            through = _find_through_item_index(items, t_val, kind, ev, ri, rows_data)
        metas.append(
            TimetableRowMeta(
                row_index=int(ri),
                t=t_val,
                kind=kind,
                json_obj=dict(r),
                display_line=format_timetable_display_line(r),
                through_item_index=int(through),
            )
        )
    return metas


def _find_through_item_index(
    items: Tuple[SimTimelineItem, ...],
    t_val: float,
    kind: str,
    ev: str,
    row_index: int,
    rows_data: List[Dict[str, Any]],
) -> int:
    """item_by_key 미스 시 행 순서 기준으로 through 인덱스 추정."""
    best = -1
    for idx, it in enumerate(items):
        if float(it.t) > float(t_val) + 1e-6:
            break
        ik = str(it.kind or "").strip().lower()
        p = it.payload
        if ik == "event" and isinstance(p, dict) and kind == "event":
            if _s_val(p.get("seq")).upper() == ev and abs(float(it.t) - t_val) <= 1e-3:
                best = idx
        elif ik == "progress" and isinstance(p, dict) and kind == "step":
            st = _s_val(p.get("status")).upper()
            el = _f_val(p.get("elapsed", 0.0), 0.0)
            if st == "RUNNING" and abs(el) <= 1e-9:
                ev2 = _s_val(p.get("event_seq") or p.get("sequence_name")).upper()
                if ev2 == ev and abs(float(it.t) - t_val) <= 1e-3:
                    best = idx
    if best >= 0:
        return best
    for idx, it in enumerate(items):
        if float(it.t) <= float(t_val) + 1e-6:
            best = idx
    return max(0, best)


def resolve_seek_through_index(
    metas: List[TimetableRowMeta],
    clicked_row_index: int,
) -> Tuple[float, int]:
    """
    클릭 행 기준 seek 목표 (t, through_item_index).
    동일 t 의 상단 행들이 모두 포함되도록 through 를 상향 조정한다.
    """
    if not metas:
        return 0.0, 0
    ri = max(0, min(int(clicked_row_index), len(metas) - 1))
    clicked = metas[ri]
    t_target = float(clicked.t)
    through = int(clicked.through_item_index)
    for m in metas[: ri + 1]:
        if abs(float(m.t) - t_target) <= 1e-6:
            through = max(through, int(m.through_item_index))
    return t_target, through


class PlaybackEnv:
    def __init__(self) -> None:
        self.now: float = 0.0


class PlaybackEngine:
    """UI 그래프 동기화용 최소 엔진( env.now 제공 )."""

    def __init__(self, final_sim_time: float) -> None:
        self.env = PlaybackEnv()
        self._final = float(final_sim_time)
        self._running = True
        self._done = False

    @property
    def is_done(self) -> bool:
        return bool(self._done)

    @property
    def is_running(self) -> bool:
        return bool(self._running and (not self._done))

    def stop(self) -> None:
        self._running = False
        self._done = True

    def _set_now(self, t: float) -> None:
        self.env.now = max(0.0, float(t))
        if self.env.now >= self._final - 1e-9:
            self._done = True


class SimTimelinePlayer:
    """
    프리런 타임라인을 wall-clock에 맞춰 재생한다.
    - emit_fn(kind, payload, screen)
    - speed_supplier() -> float
    """

    def __init__(
        self,
        results_by_screen: Dict[int, SimPreRunResult],
        emit_fn: Callable[[str, Any, int], None],
        speed_supplier: Callable[[], float],
    ) -> None:
        self._results = dict(results_by_screen or {})
        self._emit = emit_fn
        self._speed = speed_supplier
        self._lock = threading.Lock()
        self._playing = False
        self._t0_wall = 0.0
        self._t0_sim_by_screen: Dict[int, float] = {}
        self._cursor_by_screen: Dict[int, int] = {}
        self._sim_now_by_screen: Dict[int, float] = {}

    def start(self) -> None:
        with self._lock:
            self._playing = True
            self._t0_wall = time.perf_counter()
            self._t0_sim_by_screen = {scr: 0.0 for scr in self._results.keys()}
            self._cursor_by_screen = {scr: 0 for scr in self._results.keys()}
            self._sim_now_by_screen = {scr: 0.0 for scr in self._results.keys()}

    def stop(self) -> None:
        with self._lock:
            self._playing = False

    def is_playing(self) -> bool:
        with self._lock:
            return bool(self._playing)

    def sim_now(self, screen: int) -> float:
        with self._lock:
            return float(self._sim_now_by_screen.get(int(screen), 0.0))

    def cursor(self, screen: int) -> int:
        with self._lock:
            return int(self._cursor_by_screen.get(int(screen), 0))

    def seek(self, screen: int, *, target_t: float, item_cursor: int) -> None:
        """재생 커서를 ``target_t`` / ``item_cursor`` 로 옮기고 wall-clock 기준을 재설정."""
        scr = int(screen)
        t = max(0.0, float(target_t))
        ic = max(0, int(item_cursor))
        with self._lock:
            res = self._results.get(scr)
            if res is not None:
                t = min(float(res.final_sim_time), t)
                ic = min(ic, len(res.items))
            self._t0_sim_by_screen[scr] = t
            self._sim_now_by_screen[scr] = t
            self._cursor_by_screen[scr] = ic
            self._t0_wall = time.perf_counter()
            self._playing = True

    def tick(self) -> None:
        with self._lock:
            if not self._playing:
                return
            sp = 1.0
            try:
                sp = max(0.05, float(self._speed()))
            except Exception:
                sp = 1.0
            wall_dt = time.perf_counter() - float(self._t0_wall)
            for scr, res in self._results.items():
                t_base = float(self._t0_sim_by_screen.get(scr, 0.0))
                t_sim = t_base + float(wall_dt) * float(sp)
                t_sim = min(float(res.final_sim_time), float(t_sim))
                self._sim_now_by_screen[scr] = float(t_sim)

        for scr, res in self._results.items():
            t_sim = self.sim_now(scr)
            i = 0
            with self._lock:
                i = int(self._cursor_by_screen.get(scr, 0))
            items = res.items
            while i < len(items) and float(items[i].t) <= float(t_sim) + 1e-9:
                it = items[i]
                try:
                    self._emit(it.kind, it.payload, int(scr))
                except Exception:
                    pass
                i += 1
            with self._lock:
                self._cursor_by_screen[scr] = int(i)


def prerun_engine_to_timeline(
    *,
    screen: int,
    engine: Any,
    max_tick_steps: int = 2000000,
) -> SimPreRunResult:
    """
    주어진 TBSSimulationEngine 인스턴스를 가능한 빠르게 끝까지 tick() 하며,
    on_log/on_event/on_progress로 올라오는 payload를 시뮬 시간 기준으로 수집한다.
    """
    items: List[SimTimelineItem] = []

    def _t_from_payload(payload: Any) -> float:
        if isinstance(payload, dict):
            try:
                return float(str(payload.get("sim_time", "")).strip() or "0.0")
            except Exception:
                return 0.0
        return 0.0

    def on_log(line: str) -> None:
        try:
            items.append(SimTimelineItem(t=float(getattr(engine.env, "now", 0.0) or 0.0), kind="log", payload=str(line)))
        except Exception:
            items.append(SimTimelineItem(t=0.0, kind="log", payload=str(line)))

    def on_event(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="event", payload=dict(payload)))

    def on_progress(payload: Dict[str, Any]) -> None:
        items.append(SimTimelineItem(t=_t_from_payload(payload), kind="progress", payload=dict(payload)))

    try:
        engine._on_log = on_log  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        engine._on_event = on_event  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        engine._on_progress = on_progress  # type: ignore[attr-defined]
    except Exception:
        pass

    steps = 0
    while True:
        try:
            if getattr(engine, "is_done", False):
                break
            if not getattr(engine, "is_running", False):
                break
        except Exception:
            break
        try:
            engine.tick(1e6)
        except Exception:
            break
        steps += 1
        if steps >= int(max_tick_steps):
            break

    try:
        final_sim = float(getattr(engine.env, "now", 0.0) or 0.0) if getattr(engine, "env", None) is not None else 0.0
    except Exception:
        final_sim = 0.0
    try:
        te = float(getattr(engine, "_sim_total_est_sec", 0.0) or 0.0)
    except Exception:
        te = 0.0

    kind_prio = {"log": 0, "event": 1, "progress": 2}
    try:
        items.sort(key=lambda it: (float(it.t), int(kind_prio.get(str(it.kind), 9))))
    except Exception:
        pass

    return SimPreRunResult(
        screen=int(screen),
        final_sim_time=float(final_sim),
        total_est_sec=float(te),
        items=tuple(items),
    )

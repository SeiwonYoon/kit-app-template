"""장비 평면도용 점유 상태 — visibility 이벤트 미러 (시뮬 무영향).

``lam_visibility_occupancy_bus`` 가 PRIM_VISIBILITY 직후 호출한다.
슬롯/팔 hide·show 순간에 영역별 wafer 번호만 갱신한다.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .lam_wafer_prim_paths import (
    LOGICAL_SLOT_ATM_ARM,
    LOGICAL_SLOT_VTM_EE_L,
    LOGICAL_SLOT_VTM_EE_R,
)

# 평면도 UI slot_id 와 동일
MULTI_REGIONS = frozenset(
    {"foup1", "foup2", "foup3", "buffer3", "buffer4", "cs", "al1", "al2"}
)
SINGLE_REGIONS = frozenset(
    {
        "aligner",
        "pm1",
        "pm2",
        "pm3",
        "pm4",
        "pm5",
        "atm_arm",
        "vtm_left",
        "vtm_right",
    }
)
ALL_OCC_REGIONS = MULTI_REGIONS | SINGLE_REGIONS

# (screen, snapshot) — snapshot: region_id → 정렬된 표시 번호들
Listener = Callable[[int, Dict[str, Tuple[str, ...]]], None]

_FOUP_RE = re.compile(r"^foup([123])_(\d+)$")
_BUFFER_RE = re.compile(r"^buffer([34])_(\d+)$")
_COOLING_RE = re.compile(r"^cooling_(\d+)$")
_AIRLOCK_RE = re.compile(r"^airlock([12])_(\d+)$")
_CHAMBER_RE = re.compile(r"^chamber([1-5])$")


def slot_key_to_floorplan_region(slot_key: str) -> Optional[str]:
    """내부 ``slot_key`` → 평면도 region id."""
    sk = (slot_key or "").strip()
    if not sk:
        return None
    if sk == LOGICAL_SLOT_ATM_ARM:
        return "atm_arm"
    if sk == LOGICAL_SLOT_VTM_EE_L:
        return "vtm_left"
    if sk == LOGICAL_SLOT_VTM_EE_R:
        return "vtm_right"
    m = _FOUP_RE.fullmatch(sk)
    if m:
        return f"foup{m.group(1)}"
    m = _BUFFER_RE.fullmatch(sk)
    if m:
        return f"buffer{m.group(1)}"
    m = _COOLING_RE.fullmatch(sk)
    if m:
        return "cs"
    m = _AIRLOCK_RE.fullmatch(sk)
    if m:
        return f"al{m.group(1)}"
    if sk == "aligner":
        return "aligner"
    m = _CHAMBER_RE.fullmatch(sk)
    if m:
        return f"pm{m.group(1)}"
    return None


def _normalize_label(raw: Any, *, slot_key: str = "") -> str:
    s = str(raw or "").strip()
    if s:
        try:
            return f"{int(s):02d}"
        except Exception:
            return s
    try:
        from .lam_wafer_viewport_labels import cassette_style_label_for_slot_key

        fb = cassette_style_label_for_slot_key(slot_key)
        if fb:
            return str(fb)
    except Exception:
        return ""
    return ""


def _path_role(
    prim_path: str,
    ctx: Dict[str, Any],
    *,
    stage: Any = None,
) -> Optional[str]:
    """ctx·웨이퍼맵 경로와 비교해 ``slot`` / ``arm`` / None."""
    p = (prim_path or "").strip().rstrip("/")
    if not p:
        return None

    def _aliases(slot_key: str, ctx_path: str) -> set:
        out: set = set()
        cp = (ctx_path or "").strip().rstrip("/")
        if cp:
            out.add(cp)
        sk = (slot_key or "").strip()
        if not sk:
            return out
        try:
            from .lam_wafer_prim_paths import (
                load_wafer_prim_by_slot_key,
                resolve_wafer_prim_path_on_stage,
            )

            raw = (load_wafer_prim_by_slot_key().get(sk) or "").strip()
            if raw:
                out.add(raw.rstrip("/"))
            if stage is not None and raw:
                resolved = resolve_wafer_prim_path_on_stage(stage, sk, raw) or raw
                if resolved:
                    out.add(str(resolved).strip().rstrip("/"))
            if stage is not None and cp:
                resolved = resolve_wafer_prim_path_on_stage(stage, sk, cp) or cp
                if resolved:
                    out.add(str(resolved).strip().rstrip("/"))
        except Exception:
            pass
        return out

    slot_aliases = _aliases(
        str(ctx.get("slot_key") or ""),
        str(ctx.get("slot_wafer_path") or ""),
    )
    arm_aliases = _aliases(
        str(ctx.get("arm_slot_key") or ""),
        str(ctx.get("arm_wafer_path") or ""),
    )
    if p in slot_aliases:
        return "slot"
    if p in arm_aliases:
        return "arm"
    # 접미사 느슨 매칭
    for a in slot_aliases:
        if a and (p.endswith(a) or a.endswith(p)):
            return "slot"
    for a in arm_aliases:
        if a and (p.endswith(a) or a.endswith(p)):
            return "arm"
    return None


class FloorplanOccupancyTracker:
    """region → {slot_key: label}. 리스너는 snapshot 만 받는다."""

    def __init__(self, screen: int = 1) -> None:
        self._screen = max(1, int(screen or 1))
        self._lock = threading.Lock()
        self._occ: Dict[str, Dict[str, str]] = {}
        # pick 중 slot hide → arm show 사이 번호 보관
        self._arm_hold: Dict[str, str] = {}
        self._revision: int = 0
        self._listeners: List[Listener] = []

    @property
    def revision(self) -> int:
        with self._lock:
            return int(self._revision)

    def subscribe(self, listener: Listener) -> None:
        if not callable(listener):
            return
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)
            listeners = list(self._listeners)
            snap = self._snapshot_unlocked()
        # 구독 직후 현재 상태 1회 반영
        self._emit(listeners[-1:], snap)

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners = [x for x in self._listeners if x is not listener]

    def clear(self) -> None:
        with self._lock:
            self._occ.clear()
            self._arm_hold.clear()
            self._revision += 1
            listeners = list(self._listeners)
            snap = self._snapshot_unlocked()
        self._emit(listeners, snap)

    def seed_foup_baseline(
        self,
        *,
        foup_slot_keys: Optional[Sequence[str]] = None,
    ) -> int:
        """Play 시작: FOUP1~3×25 를 점유 표시 기준으로 채운다."""
        keys = list(foup_slot_keys or [])
        if not keys:
            keys = [f"foup{f}_{i}" for f in (1, 2, 3) for i in range(1, 26)]
        with self._lock:
            self._occ.clear()
            self._arm_hold.clear()
            n = 0
            for sk in keys:
                region = slot_key_to_floorplan_region(sk)
                if not region:
                    continue
                label = _normalize_label("", slot_key=sk)
                if not label:
                    continue
                self._occ.setdefault(region, {})[sk] = label
                n += 1
            self._revision += 1
            listeners = list(self._listeners)
            snap = self._snapshot_unlocked()
        self._emit(listeners, snap)
        return n

    def snapshot(self) -> Dict[str, Tuple[str, ...]]:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> Dict[str, Tuple[str, ...]]:
        out: Dict[str, Tuple[str, ...]] = {}
        for region in ALL_OCC_REGIONS:
            entries = self._occ.get(region) or {}
            if not entries:
                out[region] = ()
                continue

            def _sort_key(item: Tuple[str, str]) -> Tuple[int, int, str]:
                sk, lab = item
                m = re.search(r"_(\d+)$", sk)
                idx = int(m.group(1)) if m else 0
                try:
                    n = int(lab)
                except Exception:
                    n = 0
                return (idx, n, sk)

            labels = [lab for _, lab in sorted(entries.items(), key=_sort_key)]
            if region in SINGLE_REGIONS:
                out[region] = (labels[-1],) if labels else ()
            else:
                out[region] = tuple(labels)
        return out

    def on_visibility(
        self,
        prim_path: str,
        visible: bool,
        ctx: Dict[str, Any],
        *,
        screen: int = 1,
    ) -> None:
        """버스에서 호출 — pick/place × slot/arm × visible (라벨 트래커와 동일 의미).

        이벤트 JSON mode 는 ``build_steps_for_event`` 에서 pick/place 에 맞게
        정규화되므로, 평면도도 visible 플래그를 따른다 (3D·애니 SSOT).
        """
        si = max(1, int(screen or self._screen or 1))
        stage = None
        try:
            from .lam_csv_play_screen import get_stage_for_screen
            from .lam_extension_singleton import get_lam_extension_instance

            ext = get_lam_extension_instance()
            if ext is not None:
                stage = get_stage_for_screen(ext, si)
        except Exception:
            stage = None

        role = _path_role(prim_path, ctx, stage=stage)
        if role is None:
            return
        po = str(ctx.get("pick_or_place") or "").strip().lower()
        slot_key = str(ctx.get("slot_key") or "").strip()
        arm_sk = str(ctx.get("arm_slot_key") or "").strip()
        label = _normalize_label(ctx.get("wafer_label"), slot_key=slot_key)

        changed = False
        with self._lock:
            if po == "pick":
                if role == "slot" and not visible:
                    changed = self._remove_slot_unlocked(slot_key) or changed
                    if label and arm_sk:
                        self._arm_hold[arm_sk] = label
                elif role == "arm" and visible:
                    if not label and arm_sk:
                        label = str(self._arm_hold.get(arm_sk) or "")
                    if not label:
                        label = _normalize_label(
                            ctx.get("wafer_label"), slot_key=slot_key
                        )
                    if label:
                        changed = self._set_slot_unlocked(arm_sk, label) or changed
                        if arm_sk:
                            self._arm_hold[arm_sk] = label
            elif po == "place":
                if role == "slot" and visible:
                    if not label and arm_sk:
                        label = str(self._arm_hold.get(arm_sk) or "")
                    if label:
                        changed = self._set_slot_unlocked(slot_key, label) or changed
                elif role == "arm" and not visible:
                    changed = self._remove_slot_unlocked(arm_sk) or changed
                    if arm_sk:
                        self._arm_hold.pop(arm_sk, None)

            if changed:
                self._revision += 1
                listeners = list(self._listeners)
                snap = self._snapshot_unlocked()
            else:
                listeners = []
                snap = {}

        if listeners:
            self._emit(listeners, snap)

    def _set_slot_unlocked(self, slot_key: str, label: str) -> bool:
        region = slot_key_to_floorplan_region(slot_key)
        if not region or not label:
            return False
        bucket = self._occ.setdefault(region, {})
        if region in SINGLE_REGIONS:
            prev = dict(bucket)
            bucket.clear()
            bucket[slot_key] = label
            return prev != bucket
        if bucket.get(slot_key) == label:
            return False
        bucket[slot_key] = label
        return True

    def _remove_slot_unlocked(self, slot_key: str) -> bool:
        region = slot_key_to_floorplan_region(slot_key)
        if not region:
            return False
        bucket = self._occ.get(region)
        if not bucket:
            return False
        if slot_key in bucket:
            del bucket[slot_key]
            if not bucket:
                self._occ.pop(region, None)
            return True
        if region in SINGLE_REGIONS and bucket:
            bucket.clear()
            self._occ.pop(region, None)
            return True
        return False

    def _emit(
        self,
        listeners: Sequence[Listener],
        snap: Dict[str, Tuple[str, ...]],
    ) -> None:
        si = int(self._screen)
        for fn in listeners:
            try:
                fn(si, snap)
            except Exception:
                pass


_trackers: Dict[int, FloorplanOccupancyTracker] = {}
_trackers_lock = threading.Lock()


def get_floorplan_occupancy(screen: int = 1) -> FloorplanOccupancyTracker:
    si = max(1, int(screen or 1))
    with _trackers_lock:
        t = _trackers.get(si)
        if t is None:
            t = FloorplanOccupancyTracker(screen=si)
            _trackers[si] = t
        return t


def clear_floorplan_occupancy(screen: Optional[int] = None) -> None:
    if screen is None:
        with _trackers_lock:
            items = list(_trackers.items())
        for _si, t in items:
            try:
                t.clear()
            except Exception:
                pass
        return
    get_floorplan_occupancy(int(screen)).clear()


def seed_floorplan_foup_baseline(screen: int = 1) -> int:
    return get_floorplan_occupancy(screen).seed_foup_baseline()


__all__ = [
    "ALL_OCC_REGIONS",
    "MULTI_REGIONS",
    "SINGLE_REGIONS",
    "FloorplanOccupancyTracker",
    "clear_floorplan_occupancy",
    "get_floorplan_occupancy",
    "seed_floorplan_foup_baseline",
    "slot_key_to_floorplan_region",
]

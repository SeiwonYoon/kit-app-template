"""웨이퍼 슬롯 번호(01~25) 3D 뷰포트 라벨 — pick/place 시 팔·장비 슬롯으로 이식.

- FOUP 75슬롯 번호 표시 여부: ``lam_viewport_overlay_config.WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS``.
- CSV Play 시작 baseline: 위 설정이 True 일 때만 FOUP1~3×25 에 번호 등록.
- ``PRIM_VISIBILITY`` (pick hide SLOT / show ARM; place 는 팔 hide 시 SLOT 으로 이식) 실행 시
  ``WaferNumberLabelTracker`` 가 **동일 카세트 번호**를 팔·airlock·chamber 등으로 옮긴다.
- airlock/chamber/aligner 슬롯 인덱스(1·2)가 아니라 **FOUP 에서 올린 웨이퍼 번호**가 유지된다.
- 각 JSON 의 hide → show: hide 쪽 번호를 show prim 에 **항상** 이식 (hide 맵 항목은 제거해도 됨).
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import omni.ui as ui
from omni.ui import scene as sc
from pxr import Usd, UsdGeom

from .lam_viewport_overlay_config import WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS
from .lam_wafer_prim_paths import (
    IS_LABEL_SHOW,
    load_wafer_prim_by_slot_key,
    resolve_wafer_prim_path_on_stage,
)


_runtime_wafer_labels_ui_enabled: bool = True


def set_wafer_labels_ui_enabled(enabled: bool) -> None:
    """공정만보기 HUD 「웨이퍼번호보기」 체크박스 → 런타임 표시 on/off."""
    global _runtime_wafer_labels_ui_enabled
    _runtime_wafer_labels_ui_enabled = bool(enabled)


def get_wafer_labels_ui_enabled() -> bool:
    return bool(_runtime_wafer_labels_ui_enabled)


def wafer_label_tracking_enabled() -> bool:
    """pick/place 번호 맵 갱신 — UI 체크와 무관 (``IS_LABEL_SHOW`` 만)."""
    return bool(IS_LABEL_SHOW)


def wafer_viewport_labels_enabled() -> bool:
    """Viewport 3D 라벨 그리기 — ``IS_LABEL_SHOW`` × UI 체크박스."""
    return wafer_label_tracking_enabled() and get_wafer_labels_ui_enabled()

if TYPE_CHECKING:
    from .lam_master_stage import LamMasterStage
    from .lam_viewport import LamViewport

_PRINT_PREFIX = "[LAM/WaferLabels]"

_FRAME_SLOT = "morph.lam_control:wafer_foup_labels"
_LABEL_FONT_SIZE = 16
_LABEL_COLOR = (1.0, 1.0, 1.0, 1.0)

# 위치 추적: FOUP baseline(정적) / 팔·장비(동적) 분리 — 동적은 post_update(TBS 이동 후)

FOUP_LABEL_SLOT_KEYS: Tuple[str, ...] = tuple(
    f"foup{f}_{i}" for f in (1, 2, 3) for i in range(1, 26)
)

_foup_exclude_paths_cache: Tuple[Optional[int], frozenset[str]] = (None, frozenset())


def wafer_label_show_foup_slot_numbers() -> bool:
    """FOUP1~3×25 Viewport 번호 표시 — ``WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS``."""
    return bool(WAFER_LABEL_SHOW_FOUP_SLOT_NUMBERS)


def _foup_slot_paths_for_label_filter(
    stage: Optional[Usd.Stage],
) -> frozenset[str]:
    """그리기 제외용 FOUP 75 prim 경로 집합 (설정 False 일 때만 채움)."""
    global _foup_exclude_paths_cache
    if wafer_label_show_foup_slot_numbers():
        return frozenset()
    stage_id = id(stage) if stage is not None else None
    if _foup_exclude_paths_cache[0] == stage_id:
        return _foup_exclude_paths_cache[1]
    paths: set[str] = set()
    wm = load_wafer_prim_by_slot_key()
    for sk in FOUP_LABEL_SLOT_KEYS:
        raw = (wm.get(sk) or "").strip()
        if not raw:
            continue
        resolved = raw
        if stage is not None:
            resolved = resolve_wafer_prim_path_on_stage(stage, sk, raw) or raw
        key = _normalize_path_key(resolved)
        if key:
            paths.add(key)
    frozen = frozenset(paths)
    _foup_exclude_paths_cache = (stage_id, frozen)
    return frozen


def _filter_labels_for_draw(
    entries: List[Tuple[str, str]],
    stage: Usd.Stage,
) -> List[Tuple[str, str]]:
    if wafer_label_show_foup_slot_numbers():
        return entries
    excluded = _foup_slot_paths_for_label_filter(stage)
    if not excluded:
        return entries
    out: List[Tuple[str, str]] = []
    for path, text in entries:
        if _normalize_path_key(path) in excluded:
            continue
        out.append((path, text))
    return out


_FOUP_BUFFER_SLOT_RE = re.compile(r"^(?:foup|buffer)\d+_(\d+)$")
_COOLING_SLOT_RE = re.compile(r"^cooling_(\d+)$")

_WAFER_LABEL_CTX_KEY = "_lam_wafer_label_ctx"
_PRIM_VISIBILITY_TYPES = frozenset(
    {"PRIM_VISIBILITY", "SET_PRIM_VISIBILITY", "PRIM_HIDE", "PRIM_SHOW"}
)


def _resolve_viewport_window(viewport: Optional["LamViewport"]) -> Optional[Any]:
    """``get_frame`` 을 제공하는 ViewportWindow (전용 LAM → 활성 default → Viewport 이름)."""
    if viewport is not None:
        try:
            dedicated = getattr(viewport, "_dedicated_window", None)
            if dedicated is not None and callable(getattr(dedicated, "get_frame", None)):
                return dedicated
        except Exception:
            pass
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        win = get_active_viewport_window()
        if win is not None and callable(getattr(win, "get_frame", None)):
            return win
    except Exception:
        pass
    try:
        from omni.kit.viewport.utility import get_viewport_from_window_name  # type: ignore

        api = get_viewport_from_window_name("Viewport")
        for attr in ("viewport_window", "window", "_viewport_window", "_window"):
            cand = getattr(api, attr, None) if api is not None else None
            if cand is not None and callable(getattr(cand, "get_frame", None)):
                return cand
        if api is not None and callable(getattr(api, "get_frame", None)):
            return api
    except Exception:
        pass
    return None


def _paths_equal(a: str, b: str) -> bool:
    pa = (a or "").strip().rstrip("/")
    pb = (b or "").strip().rstrip("/")
    return bool(pa and pb and pa == pb)


def _normalize_path_key(path: str) -> str:
    return (path or "").strip().rstrip("/")


def _path_alias_set(
    prim_path: str,
    slot_key: str,
    ctx_path: str,
    *,
    stage: Optional[Usd.Stage] = None,
) -> set[str]:
    """동일 prim 을 가리킬 수 있는 경로 문자열 후보 (stage·맵·컨텍스트)."""
    aliases: set[str] = set()
    for raw in (prim_path, ctx_path):
        key = _normalize_path_key(raw)
        if key:
            aliases.add(key)
    sk = (slot_key or "").strip()
    if stage is not None and sk:
        for raw in (ctx_path, (load_wafer_prim_by_slot_key().get(sk) or "")):
            resolved = resolve_wafer_prim_path_on_stage(stage, sk, raw)
            key = _normalize_path_key(resolved)
            if key:
                aliases.add(key)
    return aliases


def _path_matches_role(
    prim_path: str,
    *,
    role: str,
    ctx: Dict[str, str],
    stage: Optional[Usd.Stage] = None,
) -> bool:
    """``prim_path`` 가 컨텍스트 slot/arm 웨이퍼 prim 과 같은 대상인지."""
    p = _normalize_path_key(prim_path)
    if not p:
        return False
    if role == "slot":
        slot_key = (ctx.get("slot_key") or "").strip()
        ctx_path = (ctx.get("slot_wafer_path") or "").strip()
    else:
        slot_key = (ctx.get("arm_slot_key") or "").strip()
        ctx_path = (ctx.get("arm_wafer_path") or "").strip()
    return p in _path_alias_set(p, slot_key, ctx_path, stage=stage)


def cassette_style_label_for_slot_key(slot_key: str) -> Optional[str]:
    """FOUP/buffer/cooling 만 카세트 번호(01~25 등). airlock/chamber/aligner 는 None."""
    sk = (slot_key or "").strip()
    m = _FOUP_BUFFER_SLOT_RE.fullmatch(sk)
    if m:
        return f"{int(m.group(1)):02d}"
    m = _COOLING_SLOT_RE.fullmatch(sk)
    if m:
        return f"{int(m.group(1)):02d}"
    return None


def make_wafer_label_step_context(
    *,
    event_name: str,
    slot_key: str,
    arm_slot_key: str,
    slot_wafer_path: str,
    arm_wafer_path: str,
    wafer_label: str = "",
) -> Dict[str, str]:
    """이벤트 JSON ``PRIM_VISIBILITY`` 스텝에 붙일 라벨 컨텍스트.

    ``wafer_label`` — CSV ``cassette_slot`` 등에서 온 표시 번호(``17`` → ``17``).
    FOUP/버퍼 슬롯 pick 시 hide/show 가 slot_key 경로와 어긋나도 번호를 유지한다.
    """
    po = "pick" if (event_name or "").strip().endswith("_pick") else "place"
    sk = (slot_key or "").strip()
    label = (wafer_label or "").strip() or (cassette_style_label_for_slot_key(sk) or "")
    return {
        "event_name": (event_name or "").strip(),
        "slot_key": sk,
        "arm_slot_key": (arm_slot_key or "").strip(),
        "slot_wafer_path": (slot_wafer_path or "").strip(),
        "arm_wafer_path": (arm_wafer_path or "").strip(),
        "pick_or_place": po,
        "wafer_label": label,
    }


def annotate_steps_with_wafer_label_context(
    steps: List[Dict[str, Any]], ctx: Dict[str, str]
) -> List[Dict[str, Any]]:
    """``PRIM_VISIBILITY`` 스텝에 이벤트 컨텍스트를 붙인다 (엔진에서 라벨 이식용)."""
    # UI 체크박스가 꺼진 상태에서 plan 을 빌드해도,
    # 나중에 켰을 때 pick/place 라벨 이식이 동작해야 하므로 컨텍스트는 항상 부착한다.
    if not ctx:
        return steps
    out: List[Dict[str, Any]] = []
    ctx_copy = dict(ctx)
    for st in steps:
        row = dict(st)
        if str(row.get("type") or "").upper() in _PRIM_VISIBILITY_TYPES:
            row[_WAFER_LABEL_CTX_KEY] = ctx_copy
        out.append(row)
    return out


def stamp_wafer_cassette_label_on_steps(
    steps: List[Dict[str, Any]], cassette_slot: int
) -> None:
    """ATM/VTM pick·place PRIM_VISIBILITY ctx — CSV ``cassette_slot`` 번호로 통일."""
    try:
        label = f"{int(cassette_slot):02d}"
    except Exception:
        label = str(cassette_slot or "").strip()
    if not label:
        return
    for st in steps:
        if not isinstance(st, dict):
            continue
        ctx = st.get(_WAFER_LABEL_CTX_KEY)
        if isinstance(ctx, dict):
            ctx["wafer_label"] = label


def _post_update_once(callback) -> None:
    """다음 post_update 에서 callback 1회 (Scene UI 갱신용)."""
    sub_ref: List[Any] = [None]

    def _on_event(_event) -> None:
        try:
            callback()
        finally:
            if sub_ref[0] is not None:
                try:
                    sub_ref[0].unsubscribe()
                except Exception:
                    pass
                sub_ref[0] = None

    try:
        import omni.kit.app as _app  # type: ignore

        stream = _app.get_app().get_post_update_event_stream()
        sub_ref[0] = stream.create_subscription_to_pop(
            _on_event, name="morph.lam_control:wafer_foup_labels_once"
        )
    except Exception:
        callback()


def _prim_is_visible(prim: Usd.Prim) -> bool:
    try:
        img = UsdGeom.Imageable(prim)
        if not img:
            return True
        vis = img.ComputeVisibility(Usd.TimeCode.Default())
        return vis != UsdGeom.Tokens.invisible
    except Exception:
        return True


def _prim_world_center(prim: Usd.Prim) -> Optional[Tuple[float, float, float]]:
    if not prim or not prim.IsValid():
        return None
    try:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        bbox = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        center = bbox.GetMidpoint()
        return (float(center[0]), float(center[1]), float(center[2]))
    except Exception:
        pass
    try:
        xform = UsdGeom.Xformable(prim)
        if xform:
            xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
            m = xform_cache.GetLocalToWorldTransform(prim)
            t = m.ExtractTranslation()
            return (float(t[0]), float(t[1]), float(t[2]))
    except Exception:
        pass
    return None


class WaferNumberLabelTracker:
    """prim 경로 ↔ 표시 번호. pick/place visibility 와 동기화."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prim_to_label: Dict[str, str] = {}
        self._arm_carried: Dict[str, str] = {}
        self._foup_baseline_paths: set[str] = set()
        self._revision: int = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return int(self._revision)

    def _bump_revision(self) -> None:
        self._revision += 1

    def _explicit_label_from_ctx(self, ctx: Dict[str, str]) -> Optional[str]:
        raw = (ctx.get("wafer_label") or "").strip()
        if not raw:
            return None
        try:
            return f"{int(raw):02d}"
        except Exception:
            return raw

    def _pop_label_for_aliases(
        self,
        prim_path: str,
        slot_key: str,
        ctx_path: str,
        *,
        stage: Optional[Usd.Stage] = None,
    ) -> Optional[str]:
        for alias in _path_alias_set(prim_path, slot_key, ctx_path, stage=stage):
            label = self._prim_to_label.pop(alias, None)
            if label:
                return label
        return None

    def clear(self) -> None:
        with self._lock:
            self._prim_to_label.clear()
            self._arm_carried.clear()
            self._foup_baseline_paths.clear()
            self._bump_revision()

    def reset_foup_baseline(
        self,
        wafer_map: Optional[Dict[str, str]] = None,
        *,
        stage: Optional[Usd.Stage] = None,
    ) -> int:
        """CSV Play 시작: 보이는 FOUP 75슬롯에만 카세트 번호 부여. 등록된 prim 수 반환."""
        with self._lock:
            self._prim_to_label.clear()
            self._arm_carried.clear()
            self._foup_baseline_paths.clear()
            wm = wafer_map or load_wafer_prim_by_slot_key()
            if wafer_label_show_foup_slot_numbers():
                for sk in FOUP_LABEL_SLOT_KEYS:
                    label = cassette_style_label_for_slot_key(sk)
                    path = (wm.get(sk) or "").strip()
                    if stage is not None and path:
                        path = resolve_wafer_prim_path_on_stage(stage, sk, path)
                    if label and path:
                        key = _normalize_path_key(path)
                        self._prim_to_label[key] = label
                        self._foup_baseline_paths.add(key)
            self._bump_revision()
            return len(self._prim_to_label)

    def _label_on_arm_for_place(self, arm_p: str, arm_sk: str) -> Optional[str]:
        """place 시 SLOT show 가 ARM hide 보다 먼저 올 때 — 팔 prim 에 아직 붙어 있는 번호."""
        if arm_sk:
            lbl = self._arm_carried.get(arm_sk)
            if lbl:
                return lbl
        key = _normalize_path_key(arm_p)
        if key:
            lbl = self._prim_to_label.get(key)
            if lbl:
                return lbl
        return None

    def _resolve_pick_label(
        self,
        ctx: Dict[str, str],
        *,
        popped: Optional[str] = None,
    ) -> Optional[str]:
        """pick 시 표시 번호 — ctx cassette_slot(explicit) > 슬롯 pop > carried > 물리 슬롯 fallback."""
        explicit = self._explicit_label_from_ctx(ctx)
        if explicit:
            return explicit
        if popped:
            return popped
        arm_sk = (ctx.get("arm_slot_key") or "").strip()
        if arm_sk:
            carried = self._arm_carried.get(arm_sk)
            if carried:
                return carried
        slot_key = (ctx.get("slot_key") or "").strip()
        return cassette_style_label_for_slot_key(slot_key)

    def _assign_label_to_arm_paths(
        self,
        label: str,
        *,
        arm_sk: str,
        arm_p: str,
        event_prim: str = "",
        stage: Optional[Usd.Stage] = None,
    ) -> None:
        """팔 carry + prim map — pick 순서(ARM show 먼저)와 관계없이 동일 번호 유지."""
        if not label:
            return
        if arm_sk:
            self._arm_carried[arm_sk] = label
        keys: set[str] = set()
        for alias in _path_alias_set(arm_p, arm_sk, arm_p, stage=stage):
            if alias:
                keys.add(alias)
        ep = _normalize_path_key(event_prim)
        if ep:
            keys.add(ep)
        for key in keys:
            self._prim_to_label[key] = label

    def on_visibility(self, prim_path: str, visible: bool, ctx: Dict[str, str]) -> None:
        """``lam_sequence_engine`` PRIM_VISIBILITY 직후 호출.

        pick: hide SLOT → 번호 보관, show ARM → 동일 번호 부여.
        place: show SLOT → 팔(또는 carried) 번호 부여, hide ARM → 맵에서만 제거.
        """
        if not wafer_label_tracking_enabled() or not ctx:
            return
        p = _normalize_path_key(prim_path)
        if not p:
            return

        stage: Optional[Usd.Stage] = None
        try:
            import omni.usd as ou  # type: ignore

            ctx_usd = ou.get_context("")
            if ctx_usd is not None:
                stage = ctx_usd.get_stage()
        except Exception:
            stage = None

        slot_p = (ctx.get("slot_wafer_path") or "").strip()
        arm_p = (ctx.get("arm_wafer_path") or "").strip()
        arm_sk = (ctx.get("arm_slot_key") or "").strip()
        po = (ctx.get("pick_or_place") or "").strip().lower()
        slot_key = (ctx.get("slot_key") or "").strip()
        explicit = self._explicit_label_from_ctx(ctx)
        cassette = explicit or cassette_style_label_for_slot_key(slot_key)

        is_slot = _path_matches_role(p, role="slot", ctx=ctx, stage=stage)
        is_arm = _path_matches_role(p, role="arm", ctx=ctx, stage=stage)
        if not is_slot and not is_arm:
            return

        changed = False
        with self._lock:
            if po == "pick":
                if is_slot and not visible:
                    popped = self._pop_label_for_aliases(
                        p, slot_key, slot_p, stage=stage
                    )
                    label = self._resolve_pick_label(ctx, popped=popped) or cassette
                    if label:
                        self._assign_label_to_arm_paths(
                            label,
                            arm_sk=arm_sk,
                            arm_p=arm_p,
                            stage=stage,
                        )
                        changed = True
                elif is_arm and visible:
                    label = self._resolve_pick_label(ctx) or cassette
                    if label:
                        self._assign_label_to_arm_paths(
                            label,
                            arm_sk=arm_sk,
                            arm_p=arm_p,
                            event_prim=p,
                            stage=stage,
                        )
                        changed = True
            elif po == "place":
                if is_slot and visible:
                    # 슬롯 show 만으로 prim 맵에 슬롯 경로를 넣지 않음 — 번호가 목적지로
                    # 먼저 점프하고 웨이퍼(팔)는 뒤늦게 따라오는 현상 방지.
                    label = self._label_on_arm_for_place(arm_p, arm_sk)
                    if not label:
                        label = explicit or cassette
                    if label:
                        if arm_sk:
                            self._arm_carried[arm_sk] = label
                        if arm_p:
                            self._assign_label_to_arm_paths(
                                label,
                                arm_sk=arm_sk,
                                arm_p=arm_p,
                                stage=stage,
                            )
                        changed = True
                elif is_arm and not visible:
                    label = self._pop_label_for_aliases(
                        p, arm_sk, arm_p, stage=stage
                    )
                    if not label and arm_sk:
                        label = self._arm_carried.get(arm_sk)
                    if label:
                        placed = False
                        for alias in _path_alias_set(
                            slot_p, slot_key, slot_p, stage=stage
                        ):
                            if alias:
                                self._prim_to_label[alias] = label
                                placed = True
                        if not placed and p:
                            self._prim_to_label[p] = label
                        if arm_sk:
                            self._arm_carried.pop(arm_sk, None)
                        changed = True
            if changed:
                self._bump_revision()
        if changed and wafer_viewport_labels_enabled():
            notify_wafer_label_tracker_changed()

    def iter_drawable_labels(self, stage: Usd.Stage) -> List[Tuple[str, str]]:
        """stage 에 존재하는 mapped prim — visibility 는 그리기 단계에서만 참고."""
        with self._lock:
            items = list(self._prim_to_label.items())
        out: List[Tuple[str, str]] = []
        for path, text in items:
            if not path or not text:
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            out.append((path, text))
        return out

    def iter_visible_labels(self, stage: Usd.Stage) -> List[Tuple[str, str]]:
        """visible 인 항목 우선; 없으면 stage 에 있는 mapped prim 전부 (visibility 오판 대비)."""
        static, dynamic = self.iter_visible_labels_split(stage)
        return list(static) + list(dynamic)

    def iter_visible_labels_split(
        self, stage: Usd.Stage
    ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        """(FOUP baseline 정적, 팔·장비 등 동적) — 동적만 post_update 에서 위치 갱신."""
        with self._lock:
            items = list(self._prim_to_label.items())
            foup_base = set(self._foup_baseline_paths)

        static_vis: List[Tuple[str, str]] = []
        static_on_stage: List[Tuple[str, str]] = []
        dynamic_vis: List[Tuple[str, str]] = []
        dynamic_on_stage: List[Tuple[str, str]] = []

        for path, text in items:
            if not path or not text:
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            is_foup = path in foup_base
            if is_foup:
                static_on_stage.append((path, text))
                if _prim_is_visible(prim):
                    static_vis.append((path, text))
            else:
                dynamic_on_stage.append((path, text))
                if _prim_is_visible(prim):
                    dynamic_vis.append((path, text))

        static_chosen = static_vis if static_vis else static_on_stage
        dynamic_chosen = dynamic_vis if dynamic_vis else dynamic_on_stage
        return (
            _filter_labels_for_draw(static_chosen, stage),
            _filter_labels_for_draw(dynamic_chosen, stage),
        )

    def mapped_prim_count(self) -> int:
        with self._lock:
            return len(self._prim_to_label)


_tracker: Optional[WaferNumberLabelTracker] = None
_active_label_overlay: Optional["LamWaferFoupViewportLabels"] = None


def teardown_wafer_viewport_labels() -> None:
    """체크 해제·확장 종료 시 SceneView·post_update poll 을 완전히 끈다."""
    global _active_label_overlay
    inst = _active_label_overlay
    _active_label_overlay = None
    if inst is not None:
        inst.destroy()


def get_wafer_label_tracker() -> WaferNumberLabelTracker:
    global _tracker
    if _tracker is None:
        _tracker = WaferNumberLabelTracker()
    return _tracker


def notify_wafer_label_tracker_changed() -> None:
    """트래커 갱신 직후 Viewport 3D 라벨 SceneView 를 다음 post_update 에 다시 그린다."""
    inst = _active_label_overlay
    if inst is None:
        return
    try:
        inst._last_tracker_revision = -1
        inst._schedule_rebuild_labels()
    except Exception:
        pass


class LamWaferFoupViewportLabels:
    """Viewport SceneView — 트래커가 가리키는 visible prim 에 3D 번호."""

    def __init__(
        self,
        *,
        viewport: Optional["LamViewport"] = None,
        master: Optional["LamMasterStage"] = None,
        ext_id: str = "",
    ) -> None:
        self._viewport = viewport
        self._master = master
        self._ext_id = (ext_id or "").strip() or _FRAME_SLOT
        self._viewport_window: Any = None
        self._scene_view: Optional[sc.SceneView] = None
        self._labels_root: Optional[sc.Transform] = None
        self._built = False
        self._sched_token = 0
        self._post_update_sub: Any = None
        self._last_drawn_count = -1
        self._last_drawn_fingerprint: Tuple[Tuple[str, str], ...] = ()
        self._last_tracker_revision = -1
        self._teardown = False
        self._label_transforms: Dict[str, Any] = {}

    def _frame_id(self) -> str:
        return self._ext_id

    def _can_operate(self) -> bool:
        return (
            not self._teardown
            and wafer_viewport_labels_enabled()
        )

    def destroy(self, *, permanent: bool = True) -> None:
        global _active_label_overlay
        if permanent:
            if _active_label_overlay is self:
                _active_label_overlay = None
            self._teardown = True
        self._sched_token += 1
        self._stop_position_poll()
        vw = self._viewport_window
        lr = self._labels_root
        sv = self._scene_view
        if vw is not None and lr is not None:
            try:
                with vw.get_frame(self._frame_id()):
                    lr.clear()
            except Exception:
                try:
                    lr.clear()
                except Exception:
                    pass
        if sv is not None and vw is not None:
            try:
                vw.viewport_api.remove_scene_view(sv)
            except Exception:
                pass
        self._scene_view = None
        self._labels_root = None
        self._viewport_window = None
        self._built = False
        self._last_drawn_count = -1
        self._last_drawn_fingerprint = ()
        self._last_tracker_revision = -1
        self._label_transforms.clear()
        if permanent:
            print(f"{_PRINT_PREFIX} SceneView removed (labels off)", flush=True)

    def _stop_position_poll(self) -> None:
        if self._post_update_sub is not None:
            try:
                self._post_update_sub.unsubscribe()
            except Exception:
                pass
            self._post_update_sub = None

    def _start_position_poll(self) -> None:
        if self._post_update_sub is not None:
            return
        try:
            import omni.kit.app as _app  # type: ignore

            post = _app.get_app().get_post_update_event_stream()

            def _on_post_update(_event) -> None:
                if not self._can_operate() or not self._built or not self._labels_root:
                    return
                # 구조(revision) → 위치: TBS translate(update) 이후 prim Xform 반영
                self._rebuild_labels()
                self._tick_dynamic_label_positions()

            self._post_update_sub = post.create_subscription_to_pop(
                _on_post_update,
                name="morph.lam_control:wafer_foup_labels",
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} post_update subscribe failed: {exc}", flush=True)

    def sync_layers(self, *, delay_frames: int = 12) -> None:
        if not wafer_viewport_labels_enabled():
            self.destroy()
            return
        self._teardown = False
        self._sched_token += 1
        token = self._sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._sched_token or not self._can_operate():
                return
            vw = _resolve_viewport_window(self._viewport)
            if vw is not None:
                self._ensure_scene(vw)
                self._schedule_rebuild_labels()
                self._start_position_poll()
                print(f"{_PRINT_PREFIX} SceneView mounted on viewport", flush=True)
                return
            if remaining > 0:
                try:
                    import omni.kit.app  # type: ignore

                    app = omni.kit.app.get_app()
                    if app is not None:
                        app.post_update(lambda: _try_mount(remaining - 1))
                        return
                except Exception:
                    pass

        _try_mount(max(0, int(delay_frames)))

    def _get_stage(self) -> Optional[Usd.Stage]:
        if self._master is not None:
            try:
                return self._master.get_stage()
            except Exception:
                pass
        try:
            import omni.usd as ou  # type: ignore

            ctx = ou.get_context("")
            if ctx is not None:
                return ctx.get_stage()
        except Exception:
            pass
        return None

    def _schedule_rebuild_labels(self) -> None:
        if not self._can_operate() or not self._built or not self._labels_root:
            return
        _post_update_once(self._rebuild_labels)

    def _ensure_scene(self, viewport_window: Any) -> None:
        if not self._can_operate():
            return
        if self._built and self._viewport_window is viewport_window:
            self._schedule_rebuild_labels()
            if self._post_update_sub is None:
                self._start_position_poll()
            return
        if self._built:
            self.destroy(permanent=False)
        self._viewport_window = viewport_window
        fid = self._frame_id()
        with viewport_window.get_frame(fid):
            with ui.ZStack():
                self._scene_view = sc.SceneView()
                with self._scene_view.scene:
                    self._labels_root = sc.Transform()
            try:
                viewport_window.viewport_api.add_scene_view(self._scene_view)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} add_scene_view failed: {exc}", flush=True)
                self._scene_view = None
                self._labels_root = None
                return
        self._built = True
        global _active_label_overlay
        _active_label_overlay = self
        self._rebuild_labels()

    def _prim_world_pos(
        self,
        prim: Usd.Prim,
        bbox_cache: Optional[UsdGeom.BBoxCache],
        *,
        xform_cache: Optional[UsdGeom.XformCache] = None,
    ) -> Optional[Tuple[float, float, float]]:
        if xform_cache is not None:
            try:
                m = xform_cache.GetLocalToWorldTransform(prim)
                t = m.ExtractTranslation()
                return (float(t[0]), float(t[1]), float(t[2]))
            except Exception:
                pass
        if bbox_cache is not None:
            try:
                bbox = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
                c = bbox.GetMidpoint()
                return (float(c[0]), float(c[1]), float(c[2]))
            except Exception:
                pass
        return _prim_world_center(prim)

    def _tick_dynamic_label_positions(self) -> None:
        """팔·장비 등 이동 prim — post_update 에서 Xform(이동 반영 후)으로 위치 갱신."""
        stage = self._get_stage()
        if not stage:
            return
        _static, dynamic = get_wafer_label_tracker().iter_visible_labels_split(stage)
        if not dynamic:
            return
        self._update_label_positions(stage, dynamic, prefer_xform_cache=True)

    def _rebuild_labels(self) -> None:
        if not self._can_operate() or not self._built or not self._labels_root:
            return
        stage = self._get_stage()
        if not stage:
            self._labels_root.clear()
            self._label_transforms.clear()
            return

        tracker = get_wafer_label_tracker()
        rev = tracker.revision
        if (
            rev == self._last_tracker_revision
            and self._label_transforms
            and self._last_tracker_revision >= 0
        ):
            return

        static_entries, dynamic_entries = tracker.iter_visible_labels_split(stage)
        entries = list(static_entries) + list(dynamic_entries)
        mapped = tracker.mapped_prim_count()
        fingerprint = tuple(sorted((a, b) for a, b in entries))

        if mapped > 0 and len(entries) == 0 and self._last_drawn_count != -2:
            print(
                f"{_PRINT_PREFIX} mapped {mapped} path(s) but none on stage — "
                f"② Open Master 후 다시 체크, 또는 lam_wafer_prim_paths.IS_TEST/경로 확인",
                flush=True,
            )
            self._last_drawn_count = -2

        if (
            len(entries) != self._last_drawn_count
            or fingerprint != self._last_drawn_fingerprint
            or rev != self._last_tracker_revision
        ):
            print(
                f"{_PRINT_PREFIX} drawing {len(entries)} label(s) "
                f"(static={len(static_entries)} dynamic={len(dynamic_entries)} "
                f"mapped={mapped} rev={rev})",
                flush=True,
            )
        self._last_drawn_count = len(entries)
        self._last_drawn_fingerprint = fingerprint
        self._last_tracker_revision = rev
        self._sync_label_structure(stage, entries)

    def _sync_label_structure(
        self, stage: Usd.Stage, entries: List[Tuple[str, str]]
    ) -> None:
        self._labels_root.clear()
        self._label_transforms.clear()

        bbox_cache: Optional[UsdGeom.BBoxCache] = None
        try:
            bbox_cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]
            )
        except Exception:
            bbox_cache = None

        with self._labels_root:
            for path_str, text in entries:
                prim = stage.GetPrimAtPath(path_str)
                if not prim or not prim.IsValid():
                    continue
                pos = self._prim_world_pos(prim, bbox_cache)
                if pos is None:
                    continue
                root = self._build_one_label(pos, text)
                if root is not None:
                    self._label_transforms[path_str] = root

    def _update_label_positions(
        self,
        stage: Usd.Stage,
        entries: List[Tuple[str, str]],
        *,
        prefer_xform_cache: bool = False,
    ) -> None:
        if not entries:
            return

        bbox_cache: Optional[UsdGeom.BBoxCache] = None
        xform_cache: Optional[UsdGeom.XformCache] = None
        if prefer_xform_cache:
            try:
                xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
                xform_cache.SetTime(Usd.TimeCode.Default())
            except Exception:
                xform_cache = None
        else:
            try:
                bbox_cache = UsdGeom.BBoxCache(
                    Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]
                )
            except Exception:
                bbox_cache = None

        for path_str, _text in entries:
            prim = stage.GetPrimAtPath(path_str)
            if not prim or not prim.IsValid():
                continue
            pos = self._prim_world_pos(
                prim, bbox_cache, xform_cache=xform_cache
            )
            if pos is None:
                continue
            node = self._label_transforms.get(path_str)
            if node is None:
                continue
            try:
                node.transform = sc.Matrix44.get_translation_matrix(*pos)
            except Exception:
                continue

    def _build_one_label(
        self, world_pos: Tuple[float, float, float], text: str
    ) -> Any:
        root = sc.Transform(
            look_at=sc.Transform.LookAt.CAMERA,
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(scale_to=sc.Space.SCREEN):
                sc.Label(
                    text,
                    size=_LABEL_FONT_SIZE,
                    color=_LABEL_COLOR,
                    alignment=ui.Alignment.CENTER,
                )
        return root


__all__ = [
    "FOUP_LABEL_SLOT_KEYS",
    "LamWaferFoupViewportLabels",
    "WaferNumberLabelTracker",
    "annotate_steps_with_wafer_label_context",
    "cassette_style_label_for_slot_key",
    "get_wafer_label_tracker",
    "notify_wafer_label_tracker_changed",
    "make_wafer_label_step_context",
    "get_wafer_labels_ui_enabled",
    "set_wafer_labels_ui_enabled",
    "stamp_wafer_cassette_label_on_steps",
    "teardown_wafer_viewport_labels",
    "wafer_label_show_foup_slot_numbers",
    "wafer_label_tracking_enabled",
    "wafer_viewport_labels_enabled",
]

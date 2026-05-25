"""웨이퍼 슬롯 번호(01~25) 3D 뷰포트 라벨 — FOUP 시작, pick/place 시 팔·장비 슬롯으로 이식.

- CSV Play 시작: FOUP1~3 각 25슬롯 prim 에만 번호 표시.
- ``PRIM_VISIBILITY`` (pick hide SLOT / show ARM, place 반대) 실행 시
  ``WaferNumberLabelTracker`` 가 **동일 카세트 번호**를 팔·airlock·chamber 등으로 옮긴다.
- airlock/chamber/aligner 슬롯 인덱스(1·2)가 아니라 **FOUP 에서 올린 웨이퍼 번호**가 유지된다.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import omni.ui as ui
from omni.ui import scene as sc
from pxr import Usd, UsdGeom

from .lam_wafer_prim_paths import IS_LABEL_SHOW, load_wafer_prim_by_slot_key


def wafer_viewport_labels_enabled() -> bool:
    """``lam_wafer_prim_paths.IS_LABEL_SHOW`` — 3D 슬롯 번호 표시 on/off."""
    return bool(IS_LABEL_SHOW)

if TYPE_CHECKING:
    from .lam_master_stage import LamMasterStage
    from .lam_viewport import LamViewport

_PRINT_PREFIX = "[LAM/WaferLabels]"

_FRAME_SLOT = "morph.lam_control:wafer_foup_labels"
_LABEL_FONT_SIZE = 16
_LABEL_COLOR = (1.0, 1.0, 1.0, 1.0)

FOUP_LABEL_SLOT_KEYS: Tuple[str, ...] = tuple(
    f"foup{f}_{i}" for f in (1, 2, 3) for i in range(1, 26)
)

_FOUP_BUFFER_SLOT_RE = re.compile(r"^(?:foup|buffer)\d+_(\d+)$")
_COOLING_SLOT_RE = re.compile(r"^cooling_(\d+)$")

_WAFER_LABEL_CTX_KEY = "_lam_wafer_label_ctx"
_PRIM_VISIBILITY_TYPES = frozenset(
    {"PRIM_VISIBILITY", "SET_PRIM_VISIBILITY", "PRIM_HIDE", "PRIM_SHOW"}
)


def _resolve_viewport_window(viewport: Optional["LamViewport"]) -> Optional[Any]:
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
    return None


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
) -> Dict[str, str]:
    po = "pick" if (event_name or "").strip().endswith("_pick") else "place"
    return {
        "event_name": (event_name or "").strip(),
        "slot_key": (slot_key or "").strip(),
        "arm_slot_key": (arm_slot_key or "").strip(),
        "slot_wafer_path": (slot_wafer_path or "").strip(),
        "arm_wafer_path": (arm_wafer_path or "").strip(),
        "pick_or_place": po,
    }


def annotate_steps_with_wafer_label_context(
    steps: List[Dict[str, Any]], ctx: Dict[str, str]
) -> List[Dict[str, Any]]:
    """``PRIM_VISIBILITY`` 스텝에 이벤트 컨텍스트를 붙인다 (엔진에서 라벨 이식용)."""
    if not wafer_viewport_labels_enabled() or not ctx:
        return steps
    out: List[Dict[str, Any]] = []
    ctx_copy = dict(ctx)
    for st in steps:
        row = dict(st)
        if str(row.get("type") or "").upper() in _PRIM_VISIBILITY_TYPES:
            row[_WAFER_LABEL_CTX_KEY] = ctx_copy
        out.append(row)
    return out


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
        self._prim_to_label: Dict[str, str] = {}
        self._arm_carried: Dict[str, str] = {}

    def clear(self) -> None:
        self._prim_to_label.clear()
        self._arm_carried.clear()

    def reset_foup_baseline(self, wafer_map: Optional[Dict[str, str]] = None) -> None:
        """CSV Play 시작: 보이는 FOUP 75슬롯에만 카세트 번호 부여."""
        self.clear()
        wm = wafer_map or load_wafer_prim_by_slot_key()
        for sk in FOUP_LABEL_SLOT_KEYS:
            label = cassette_style_label_for_slot_key(sk)
            path = (wm.get(sk) or "").strip()
            if label and path:
                self._prim_to_label[path] = label

    def on_visibility(self, prim_path: str, visible: bool, ctx: Dict[str, str]) -> None:
        """``lam_sequence_engine`` PRIM_VISIBILITY 직후 호출."""
        if not wafer_viewport_labels_enabled() or not ctx:
            return
        p = (prim_path or "").strip()
        if not p:
            return

        slot_p = (ctx.get("slot_wafer_path") or "").strip()
        arm_p = (ctx.get("arm_wafer_path") or "").strip()
        arm_sk = (ctx.get("arm_slot_key") or "").strip()
        po = (ctx.get("pick_or_place") or "").strip().lower()
        slot_key = (ctx.get("slot_key") or "").strip()
        cassette = cassette_style_label_for_slot_key(slot_key)

        if slot_p and p == slot_p:
            if po == "pick" and not visible:
                label = self._prim_to_label.pop(p, None)
                if label:
                    if arm_sk:
                        self._arm_carried[arm_sk] = label
                elif cassette and arm_sk:
                    self._arm_carried[arm_sk] = cassette
            elif po == "place" and visible:
                label = self._arm_carried.get(arm_sk) if arm_sk else None
                if not label:
                    label = cassette
                if label:
                    self._prim_to_label[p] = label
            return

        if arm_p and p == arm_p:
            if po == "pick" and visible:
                label = self._arm_carried.get(arm_sk) if arm_sk else None
                if not label:
                    label = cassette
                    if label and arm_sk:
                        self._arm_carried[arm_sk] = label
                if label:
                    self._prim_to_label[p] = label
            elif po == "place" and not visible:
                self._prim_to_label.pop(p, None)

    def iter_visible_labels(self, stage: Usd.Stage) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for path, text in self._prim_to_label.items():
            if not path or not text:
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            if not _prim_is_visible(prim):
                continue
            out.append((path, text))
        return out


_tracker: Optional[WaferNumberLabelTracker] = None


def get_wafer_label_tracker() -> WaferNumberLabelTracker:
    global _tracker
    if _tracker is None:
        _tracker = WaferNumberLabelTracker()
    return _tracker


class LamWaferFoupViewportLabels:
    """Viewport SceneView — 트래커가 가리키는 visible prim 에 3D 번호."""

    def __init__(
        self,
        *,
        viewport: Optional["LamViewport"] = None,
        master: Optional["LamMasterStage"] = None,
    ) -> None:
        self._viewport = viewport
        self._master = master
        self._viewport_window: Any = None
        self._scene_view: Optional[sc.SceneView] = None
        self._labels_root: Optional[sc.Transform] = None
        self._built = False
        self._sched_token = 0
        self._post_update_sub: Any = None

    def destroy(self) -> None:
        self._sched_token += 1
        self._stop_position_poll()
        if self._scene_view and self._viewport_window:
            try:
                self._viewport_window.viewport_api.remove_scene_view(self._scene_view)
            except Exception:
                pass
        self._scene_view = None
        self._labels_root = None
        self._viewport_window = None
        self._built = False

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

            stream = _app.get_app().get_post_update_event_stream()

            def _on_post_update(_event) -> None:
                if not self._built or not self._labels_root:
                    return
                self._rebuild_labels()

            self._post_update_sub = stream.create_subscription_to_pop(
                _on_post_update,
                name="morph.lam_control:wafer_foup_labels_poll",
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} post_update subscribe failed: {exc}", flush=True)

    def sync_layers(self, *, delay_frames: int = 12) -> None:
        if not wafer_viewport_labels_enabled():
            self.destroy()
            return
        self._sched_token += 1
        token = self._sched_token

        def _try_mount(remaining: int) -> None:
            if token != self._sched_token:
                return
            vw = _resolve_viewport_window(self._viewport)
            if vw is not None:
                self._ensure_scene(vw)
                self._rebuild_labels()
                self._start_position_poll()
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

    def _ensure_scene(self, viewport_window: Any) -> None:
        if self._built and self._viewport_window is viewport_window:
            return
        if self._built:
            self.destroy()
        self._viewport_window = viewport_window
        with viewport_window.get_frame(_FRAME_SLOT):
            with ui.ZStack():
                self._scene_view = sc.SceneView()
                with self._scene_view.scene:
                    self._labels_root = sc.Transform()
            viewport_window.viewport_api.add_scene_view(self._scene_view)
        self._built = True

    def _rebuild_labels(self) -> None:
        if not self._built or not self._labels_root:
            return
        stage = self._get_stage()
        if not stage:
            self._labels_root.clear()
            return

        entries = get_wafer_label_tracker().iter_visible_labels(stage)
        self._labels_root.clear()

        with self._labels_root:
            for path_str, text in entries:
                prim = stage.GetPrimAtPath(path_str)
                if not prim or not prim.IsValid():
                    continue
                pos = _prim_world_center(prim)
                if pos is None:
                    continue
                self._build_one_label(pos, text)

    def _build_one_label(
        self, world_pos: Tuple[float, float, float], text: str
    ) -> None:
        root = sc.Transform(
            transform=sc.Matrix44.get_translation_matrix(*world_pos),
        )
        with root:
            with sc.Transform(look_at=sc.Transform.LookAt.CAMERA):
                with sc.Transform(scale_to=sc.Space.SCREEN):
                    sc.Label(
                        text,
                        size=_LABEL_FONT_SIZE,
                        color=_LABEL_COLOR,
                        alignment=ui.Alignment.CENTER,
                    )


__all__ = [
    "FOUP_LABEL_SLOT_KEYS",
    "LamWaferFoupViewportLabels",
    "WaferNumberLabelTracker",
    "annotate_steps_with_wafer_label_context",
    "cassette_style_label_for_slot_key",
    "get_wafer_label_tracker",
    "make_wafer_label_step_context",
    "wafer_viewport_labels_enabled",
]

"""LAM 장비 평면도 UI — CSV 시뮬 재생창 (배속 ↔ 타임라인 사이).

점유 색/번호는 ``lam_floorplan_occupancy`` 스냅샷을 읽기 전용으로 반영한다.
시뮬 스케줄·visibility 자체는 변경하지 않는다.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .lam_floorplan_occupancy import MULTI_REGIONS, SINGLE_REGIONS

# 평면도 논리 좌표 (0~1000 x, 0~560 y)
_FW = 1000.0
_FH = 560.0

# (id, label, cx, cy, w, h) — AL1 먼저, C.S. 는 나중에 그려 AL1 왼쪽 위에 겹침
_SLOT_BOXES: Tuple[Tuple[str, str, float, float, float, float], ...] = (
    ("foup1", "FOUP 1", 70, 95, 100, 72),
    ("foup2", "FOUP 2", 70, 210, 100, 72),
    ("foup3", "FOUP 3", 70, 325, 100, 72),
    ("aligner", "Aligner", 200, 95, 92, 56),
    ("buffer3", "버퍼 3", 355, 140, 92, 70),
    ("buffer4", "버퍼 4", 355, 330, 92, 70),
    ("al1", "AL1", 490, 185, 92, 72),
    ("al2", "AL2", 490, 310, 92, 72),
    # ATM 쪽(왼쪽) · AL1 왼쪽 위 겹침
    ("cs", "C.S.", 430, 145, 72, 48),
    ("pm1", "PM1", 740, 70, 88, 64),
    ("pm2", "PM2", 880, 130, 88, 64),
    ("pm3", "PM3", 920, 270, 88, 64),
    ("pm4", "PM4", 880, 410, 88, 64),
    ("pm5", "PM5", 740, 460, 88, 64),
)

# ATM 본체(라벨 전용) — tip 은 본체 **밖** 왼쪽에 두어 "ATM" 글자가 가려지지 않게
_ATM_BODY = ("atm_body", "ATM", 250, 235, 100, 44)
_ATM_TIP = ("atm_arm", "●", 175, 235, 30, 30)

# VTM 본체 — 좌/우 tip 은 본체 바깥
_VTM_BODY = ("vtm_body", "VTM", 730, 270, 96, 72)
_VTM_LEFT = ("vtm_left", "◀", 645, 270, 34, 34)
_VTM_RIGHT = ("vtm_right", "▶", 815, 270, 34, 34)

_BOX_STYLE = {
    "background_color": 0xFF2A3140,
    "border_width": 1,
    "border_color": 0xFF6A7A90,
    "border_radius": 4,
}
_TIP_STYLE = {
    "background_color": 0xFF3A4558,
    "border_width": 1,
    "border_color": 0xFF8AA0B8,
    "border_radius": 12,
}
_CS_STYLE = {
    "background_color": 0xFF3A3848,
    "border_width": 1,
    "border_color": 0xFF9A8AB0,
    "border_radius": 4,
}
_OCC_BOX_STYLE = {
    "background_color": 0xFF1E5A48,
    "border_width": 1,
    "border_color": 0xFF5AD0A0,
    "border_radius": 4,
}
_OCC_TIP_STYLE = {
    "background_color": 0xFF2A6A50,
    "border_width": 1,
    "border_color": 0xFF7AE0B0,
    "border_radius": 12,
}
_OCC_CS_STYLE = {
    "background_color": 0xFF3A4A58,
    "border_width": 1,
    "border_color": 0xFF80B0D0,
    "border_radius": 4,
}
_BG_STYLE = {"background_color": 0xFF141820}
_LABEL_STYLE = {"color": 0xFFE8EEF8, "font_size": 12}
_LABEL_OCC_STYLE = {"color": 0xFFF0FFF8, "font_size": 11}
_LABEL_MULTI_STYLE = {"color": 0xFFF0FFF8, "font_size": 10}
_CONNECTOR_STYLE = {"background_color": 0xFF8AA0B8, "border_radius": 2}

_BASE_LABELS: Dict[str, str] = {
    sid: label for sid, label, *_ in _SLOT_BOXES
}
_BASE_LABELS.update(
    {
        _ATM_BODY[0]: _ATM_BODY[1],
        _ATM_TIP[0]: _ATM_TIP[1],
        _VTM_BODY[0]: _VTM_BODY[1],
        _VTM_LEFT[0]: _VTM_LEFT[1],
        _VTM_RIGHT[0]: _VTM_RIGHT[1],
    }
)


def _scale_rect(
    cx: float,
    cy: float,
    w: float,
    h: float,
    *,
    pix_w: float,
    pix_h: float,
) -> Tuple[float, float, float, float]:
    sx = float(pix_w) / _FW
    sy = float(pix_h) / _FH
    pw = max(12.0, float(w) * sx)
    ph = max(12.0, float(h) * sy)
    left = float(cx) * sx - pw * 0.5
    top = float(cy) * sy - ph * 0.5
    return left, top, pw, ph


def _place_labeled_box(
    ui: Any,
    *,
    slot_id: str,
    label: str,
    left: float,
    top: float,
    width: float,
    height: float,
    style: Dict[str, Any],
    out_widgets: Dict[str, Any],
) -> None:
    with ui.Placer(offset_x=float(left), offset_y=float(top)):
        with ui.ZStack(width=float(width), height=float(height)):
            rect = ui.Rectangle(style=dict(style))
            lbl = ui.Label(
                str(label),
                alignment=ui.Alignment.CENTER,
                style=dict(_LABEL_STYLE),
                word_wrap=True,
            )
            out_widgets[slot_id] = {
                "rect": rect,
                "label": lbl,
                "kind": "slot",
                "base_label": str(label),
                "idle_style": dict(style),
            }


def _place_connector(
    ui: Any,
    *,
    cx: float,
    cy: float,
    w: float,
    h: float,
    pix_w: float,
    pix_h: float,
) -> None:
    left, top, pw, ph = _scale_rect(cx, cy, w, h, pix_w=pix_w, pix_h=pix_h)
    with ui.Placer(offset_x=left, offset_y=top):
        ui.Rectangle(width=pw, height=max(2.0, ph), style=dict(_CONNECTOR_STYLE))


def _format_region_text(region_id: str, wafers: Tuple[str, ...]) -> str:
    base = _BASE_LABELS.get(region_id, region_id)
    if not wafers:
        return base
    if region_id in SINGLE_REGIONS:
        tip_glyphs = {"atm_arm": "●", "vtm_left": "◀", "vtm_right": "▶"}
        if region_id in tip_glyphs:
            return f"{tip_glyphs[region_id]}{wafers[0]}"
        return f"{base}\n{wafers[0]}"
    # multi: 영역 안 번호 나열 (길면 일부 +잔여)
    nums = list(wafers)
    if len(nums) <= 10:
        joined = " ".join(nums)
    else:
        joined = " ".join(nums[:8]) + f" +{len(nums) - 8}"
    return f"{base}\n{joined}"


def _idle_style_for(region_id: str) -> Dict[str, Any]:
    if region_id == "cs":
        return dict(_CS_STYLE)
    if region_id in ("atm_arm", "vtm_left", "vtm_right"):
        return dict(_TIP_STYLE)
    return dict(_BOX_STYLE)


def _occ_style_for(region_id: str) -> Dict[str, Any]:
    if region_id == "cs":
        return dict(_OCC_CS_STYLE)
    if region_id in ("atm_arm", "vtm_left", "vtm_right"):
        return dict(_OCC_TIP_STYLE)
    return dict(_OCC_BOX_STYLE)


def apply_floorplan_occupancy_snapshot(
    handle: Optional[Dict[str, Any]],
    snapshot: Dict[str, Tuple[str, ...]],
) -> None:
    """메인 스레드에서 평면도 위젯 텍스트/색만 갱신."""
    if not handle:
        return
    slots = handle.get("slots") or {}
    snap = snapshot or {}
    for sid, widgets in slots.items():
        if not isinstance(widgets, dict):
            continue
        if sid in ("atm_body", "vtm_body"):
            continue
        wafers = tuple(snap.get(sid) or ())
        base = str(widgets.get("base_label") or _BASE_LABELS.get(sid, sid))
        text = _format_region_text(sid, wafers) if sid in (
            MULTI_REGIONS | SINGLE_REGIONS
        ) else base
        occupied = bool(wafers)
        rect = widgets.get("rect")
        lbl = widgets.get("label")
        try:
            if rect is not None:
                style = (
                    _occ_style_for(sid)
                    if occupied
                    else (widgets.get("idle_style") or _idle_style_for(sid))
                )
                rect.set_style(dict(style))
        except Exception:
            try:
                if rect is not None:
                    rect.style = dict(
                        _occ_style_for(sid) if occupied else _idle_style_for(sid)
                    )
            except Exception:
                pass
        try:
            if lbl is not None:
                lbl.text = text
                if occupied:
                    st = (
                        dict(_LABEL_MULTI_STYLE)
                        if sid in MULTI_REGIONS
                        else dict(_LABEL_OCC_STYLE)
                    )
                else:
                    st = dict(_LABEL_STYLE)
                try:
                    lbl.set_style(st)
                except Exception:
                    lbl.style = st
        except Exception:
            pass


def build_equipment_floorplan_ui(
    ui: Any,
    *,
    height: float = 200.0,
    width: Optional[float] = None,
) -> Dict[str, Any]:
    """
    현재 UI 컨텍스트에 평면도 ZStack 을 붙인다.

    Returns:
        ``{"root": ZStack, "slots": {slot_id: {...}}}`` — 점유 색 갱신용.
    """
    pix_h = max(120.0, float(height))
    pix_w = float(width) if width is not None and float(width) > 1.0 else 620.0
    widgets: Dict[str, Any] = {}

    with ui.ZStack(height=pix_h) as root:
        ui.Rectangle(style=dict(_BG_STYLE))

        # 1) 고정 슬롯 (AL1 → … → C.S. 가 나중에 올라와 AL1 왼쪽 위에 겹침)
        for sid, label, cx, cy, w, h in _SLOT_BOXES:
            left, top, pw, ph = _scale_rect(cx, cy, w, h, pix_w=pix_w, pix_h=pix_h)
            st = dict(_CS_STYLE) if sid == "cs" else dict(_BOX_STYLE)
            _place_labeled_box(
                ui,
                slot_id=sid,
                label=label,
                left=left,
                top=top,
                width=pw,
                height=ph,
                style=st,
                out_widgets=widgets,
            )

        # 2) 연결선 (본체/라벨 아래 레이어)
        _place_connector(ui, cx=210, cy=235, w=40, h=5, pix_w=pix_w, pix_h=pix_h)
        _place_connector(ui, cx=685, cy=270, w=36, h=5, pix_w=pix_w, pix_h=pix_h)
        _place_connector(ui, cx=775, cy=270, w=36, h=5, pix_w=pix_w, pix_h=pix_h)

        # 3) ATM / VTM 본체·tip (tip 은 본체 밖 — 라벨 비가림)
        for spec, style in (
            (_ATM_BODY, _BOX_STYLE),
            (_ATM_TIP, _TIP_STYLE),
            (_VTM_BODY, _BOX_STYLE),
            (_VTM_LEFT, _TIP_STYLE),
            (_VTM_RIGHT, _TIP_STYLE),
        ):
            sid, label, cx, cy, w, h = spec
            left, top, pw, ph = _scale_rect(cx, cy, w, h, pix_w=pix_w, pix_h=pix_h)
            _place_labeled_box(
                ui,
                slot_id=sid,
                label=label,
                left=left,
                top=top,
                width=pw,
                height=ph,
                style=dict(style),
                out_widgets=widgets,
            )

    return {"root": root, "slots": widgets, "pix_w": pix_w, "pix_h": pix_h}


def bind_floorplan_occupancy_ui(
    handle: Dict[str, Any],
    *,
    screen: int = 1,
) -> None:
    """점유 트래커 → 평면도 UI (메인 스레드 마샬링)."""
    si = max(1, int(screen or 1))
    prev = handle.get("_occ_listener")
    try:
        from .lam_floorplan_occupancy import get_floorplan_occupancy

        tracker = get_floorplan_occupancy(si)
        if prev is not None:
            try:
                tracker.unsubscribe(prev)
            except Exception:
                pass

        def _on_occ(_screen: int, snap: Dict[str, Tuple[str, ...]]) -> None:
            try:
                from .kit_main_dispatch import schedule_on_main_thread

                schedule_on_main_thread(
                    lambda: apply_floorplan_occupancy_snapshot(handle, snap)
                )
            except Exception:
                try:
                    apply_floorplan_occupancy_snapshot(handle, snap)
                except Exception:
                    pass

        handle["_occ_listener"] = _on_occ
        handle["_occ_screen"] = si
        tracker.subscribe(_on_occ)
    except Exception:
        pass


def unbind_floorplan_occupancy_ui(handle: Optional[Dict[str, Any]]) -> None:
    if not handle:
        return
    prev = handle.pop("_occ_listener", None)
    si = int(handle.pop("_occ_screen", 1) or 1)
    if prev is None:
        return
    try:
        from .lam_floorplan_occupancy import get_floorplan_occupancy

        get_floorplan_occupancy(si).unsubscribe(prev)
    except Exception:
        pass


def mount_equipment_floorplan_ui(
    ui: Any,
    host: Any,
    *,
    height: float = 200.0,
    screen: Optional[int] = None,
) -> Dict[str, Any]:
    """``host`` 에 평면도 블록을 마운트 (배속 아래 · 타임라인 위)."""
    ui.Label("장비 배치도 — 점유(번호/색)는 시뮬 visibility 와 동기", height=16)
    handle = build_equipment_floorplan_ui(ui, height=float(height))
    si = 1
    try:
        if screen is not None:
            si = max(1, int(screen))
        else:
            si = max(1, int(getattr(host, "screen", 1) or 1))
    except Exception:
        si = 1
    bind_floorplan_occupancy_ui(handle, screen=si)
    try:
        host._floorplan_ui = handle
    except Exception:
        pass
    return handle


__all__ = [
    "apply_floorplan_occupancy_snapshot",
    "bind_floorplan_occupancy_ui",
    "build_equipment_floorplan_ui",
    "mount_equipment_floorplan_ui",
    "unbind_floorplan_occupancy_ui",
]

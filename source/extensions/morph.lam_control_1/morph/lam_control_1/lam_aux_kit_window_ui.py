"""Viewport HUD 「창 표시」 체크박스 ↔ Kit 보조 창 visible (TBS ebs_control_panel_ui 패턴)."""

from __future__ import annotations

from typing import Any, List, Tuple

# (ext 모델 속성, HUD 라벨, resolver key)
_AUX_KIT_WINDOW_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("_ui_show_lam_usd_model", "Multi-USD Load", "usd"),
    ("_ui_show_lam_sequence_model", "Sequence Editor", "sequence"),
)

# Viewport 오버레이·Federation 테스트 (상수 기본값, 런타임 토글)
_AUX_VIEWPORT_PANEL_SPECS: Tuple[Tuple[str, str], ...] = (
    ("_ui_show_viewport_floorplan_model", "장비배치도"),
    ("_ui_show_viewport_status_model", "STATUS 패널"),
    ("_ui_show_federation_test_model", "Federation 테스트"),
)

_CHECKBOX_STYLE = {"color": 0xFFFFFFFF}


def _set_kit_window_visible(win: Any, visible: bool) -> None:
    if win is None:
        return
    try:
        win.visible = bool(visible)
        return
    except Exception:
        pass
    try:
        if hasattr(win, "set_visible"):
            win.set_visible(bool(visible))
    except Exception:
        pass


def _lam_window(ext: Any) -> Any:
    return getattr(ext, "_lam_window", None) or getattr(ext, "_window", None)


def _csv_play_screen_indices(ext: Any) -> List[int]:
    lam_win = _lam_window(ext)
    if lam_win is None:
        try:
            from .lam_sim_control_defaults import default_csv_play_screen_count

            return list(range(1, int(default_csv_play_screen_count()) + 1))
        except Exception:
            return [1]
    try:
        screens = sorted(int(k) for k in getattr(lam_win, "_csv_sim_windows", {}).keys())
        if screens:
            return screens
    except Exception:
        pass
    try:
        from .lam_sim_control_defaults import default_csv_play_screen_count

        return list(range(1, int(default_csv_play_screen_count()) + 1))
    except Exception:
        return [1]


def _csv_play_model_attr(screen: int) -> str:
    return f"_ui_show_lam_csv_play_screen_{int(screen)}_model"


def ensure_csv_play_screen_model(ext: Any, screen: int) -> Any:
    """화면별 CSV 재생창 표시 모델 — 없으면 기본값으로 생성."""
    si = max(1, int(screen))
    attr = _csv_play_model_attr(si)
    mdl = getattr(ext, attr, None)
    if mdl is not None:
        return mdl
    try:
        import omni.ui as ui

        from .lam_sim_control_defaults import UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT

        default_on = bool(UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT)
        mdl = ui.SimpleBoolModel(default_on)
        setattr(ext, attr, mdl)
        mdl.add_value_changed_fn(
            lambda _m, s=si: _on_csv_play_screen_visibility_changed(ext, s)
        )
        return mdl
    except Exception:
        return None


def init_lam_aux_kit_window_models(ext: Any) -> None:
    """Extension startup — HUD 창 표시 체크박스 모델."""
    try:
        import omni.ui as ui
    except Exception:
        return
    try:
        from .lam_sim_control_defaults import (
            FEDERATION_TEST_WINDOW_AUTO_SHOW,
            SHOW_VIEWPORT_FLOORPLAN_PANEL,
            SHOW_VIEWPORT_STATUS_PANEL,
            UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT,
            UI_SHOW_LAM_SEQUENCE_EDITOR_DEFAULT,
            UI_SHOW_LAM_USD_WINDOW_DEFAULT,
        )
    except Exception:
        UI_SHOW_LAM_USD_WINDOW_DEFAULT = False
        UI_SHOW_LAM_SEQUENCE_EDITOR_DEFAULT = False
        UI_SHOW_LAM_CSV_PLAY_WINDOW_DEFAULT = True
        SHOW_VIEWPORT_FLOORPLAN_PANEL = False
        SHOW_VIEWPORT_STATUS_PANEL = False
        FEDERATION_TEST_WINDOW_AUTO_SHOW = False

    if getattr(ext, "_ui_show_lam_usd_model", None) is None:
        ext._ui_show_lam_usd_model = ui.SimpleBoolModel(
            bool(UI_SHOW_LAM_USD_WINDOW_DEFAULT)
        )
        try:
            ext._ui_show_lam_usd_model.add_value_changed_fn(
                lambda m: _on_aux_kit_window_visibility_changed(ext, "usd", m)
            )
        except Exception:
            pass

    if getattr(ext, "_ui_show_lam_sequence_model", None) is None:
        ext._ui_show_lam_sequence_model = ui.SimpleBoolModel(
            bool(UI_SHOW_LAM_SEQUENCE_EDITOR_DEFAULT)
        )
        try:
            ext._ui_show_lam_sequence_model.add_value_changed_fn(
                lambda m: _on_aux_kit_window_visibility_changed(ext, "sequence", m)
            )
        except Exception:
            pass

    if getattr(ext, "_ui_show_viewport_floorplan_model", None) is None:
        ext._ui_show_viewport_floorplan_model = ui.SimpleBoolModel(
            bool(SHOW_VIEWPORT_FLOORPLAN_PANEL)
        )
        try:
            from .lam_viewport_floorplan_panel import (
                set_viewport_floorplan_panel_feature_rt,
            )

            set_viewport_floorplan_panel_feature_rt(bool(SHOW_VIEWPORT_FLOORPLAN_PANEL))
        except Exception:
            pass
        try:
            ext._ui_show_viewport_floorplan_model.add_value_changed_fn(
                lambda _m: _on_viewport_floorplan_visibility_changed(ext)
            )
        except Exception:
            pass

    if getattr(ext, "_ui_show_viewport_status_model", None) is None:
        ext._ui_show_viewport_status_model = ui.SimpleBoolModel(
            bool(SHOW_VIEWPORT_STATUS_PANEL)
        )
        try:
            lam = _lam_window(ext)
            if lam is not None:
                lam._show_viewport_status_panel_rt = bool(SHOW_VIEWPORT_STATUS_PANEL)
        except Exception:
            pass
        try:
            ext._ui_show_viewport_status_model.add_value_changed_fn(
                lambda _m: _on_viewport_status_visibility_changed(ext)
            )
        except Exception:
            pass

    if getattr(ext, "_ui_show_federation_test_model", None) is None:
        ext._ui_show_federation_test_model = ui.SimpleBoolModel(
            bool(FEDERATION_TEST_WINDOW_AUTO_SHOW)
        )
        try:
            ext._ui_show_federation_test_model.add_value_changed_fn(
                lambda _m: _on_federation_test_visibility_changed(ext)
            )
        except Exception:
            pass

    for si in _csv_play_screen_indices(ext):
        ensure_csv_play_screen_model(ext, si)


def _resolve_aux_kit_windows(ext: Any, which: str) -> List[Any]:
    lam_win = _lam_window(ext)
    if which == "usd":
        w = getattr(lam_win, "_window", None) if lam_win is not None else None
        return [w] if w is not None else []
    if which == "sequence":
        editor = getattr(lam_win, "_sequence_editor", None) if lam_win is not None else None
        w = getattr(editor, "_window", None) if editor is not None else None
        return [w] if w is not None else []
    if which == "federation_test":
        fed = (
            getattr(lam_win, "_federation_test_window", None)
            if lam_win is not None
            else None
        )
        w = getattr(fed, "_window", None) if fed is not None else None
        return [w] if w is not None else []
    if which.startswith("csv_play:"):
        try:
            si = int(which.split(":", 1)[1])
        except Exception:
            return []
        if lam_win is None:
            return []
        csv_win = getattr(lam_win, "_csv_sim_windows", {}).get(si)
        if csv_win is None:
            return []
        w = getattr(csv_win, "_window", None)
        return [w] if w is not None else []
    return []


def sync_aux_kit_window_visibility(ext: Any) -> None:
    """HUD 체크박스 모델 → Kit 보조 창 visible."""
    for model_attr, _label, which in _AUX_KIT_WINDOW_SPECS:
        mdl = getattr(ext, model_attr, None)
        if mdl is None:
            continue
        try:
            visible = bool(mdl.as_bool)
        except Exception:
            visible = False
        for win in _resolve_aux_kit_windows(ext, which):
            _set_kit_window_visible(win, visible)

    for si in _csv_play_screen_indices(ext):
        mdl = ensure_csv_play_screen_model(ext, si)
        if mdl is None:
            continue
        try:
            visible = bool(mdl.as_bool)
        except Exception:
            visible = True
        for win in _resolve_aux_kit_windows(ext, f"csv_play:{si}"):
            _set_kit_window_visible(win, visible)

    # Federation 테스트 — 창이 이미 있을 때만 visible 반영 (생성은 change 핸들러)
    _sync_federation_test_visibility(ext, create_if_needed=False)
    # Viewport 패널은 change 시 적용; startup sync 시 1회 반영
    _apply_viewport_floorplan_visibility(ext)
    _apply_viewport_status_visibility(ext)


def _on_aux_kit_window_visibility_changed(
    ext: Any, which: str, _model: Any = None
) -> None:
    for model_attr, _label, key in _AUX_KIT_WINDOW_SPECS:
        if key != which:
            continue
        mdl = getattr(ext, model_attr, None)
        if mdl is None:
            return
        try:
            visible = bool(mdl.as_bool)
        except Exception:
            visible = False
        for win in _resolve_aux_kit_windows(ext, which):
            _set_kit_window_visible(win, visible)
        return


def _on_csv_play_screen_visibility_changed(ext: Any, screen: int) -> None:
    si = max(1, int(screen))
    mdl = ensure_csv_play_screen_model(ext, si)
    if mdl is None:
        return
    try:
        visible = bool(mdl.as_bool)
    except Exception:
        visible = True
    for win in _resolve_aux_kit_windows(ext, f"csv_play:{si}"):
        _set_kit_window_visible(win, visible)


def _apply_viewport_floorplan_visibility(ext: Any) -> None:
    mdl = getattr(ext, "_ui_show_viewport_floorplan_model", None)
    if mdl is None:
        return
    try:
        visible = bool(mdl.as_bool)
    except Exception:
        visible = False
    try:
        from .lam_viewport_floorplan_panel import (
            force_remove_floorplan_panels,
            set_viewport_floorplan_panel_feature_rt,
        )
        from .lam_viewport_overlay_state import set_toggle_floorplan_panel

        set_viewport_floorplan_panel_feature_rt(visible)
        if visible:
            set_toggle_floorplan_panel(True)
            lam = _lam_window(ext)
            if lam is None:
                return
            for si in _csv_play_screen_indices(ext):
                csv_win = getattr(lam, "_csv_sim_windows", {}).get(si)
                if csv_win is None:
                    continue
                if hasattr(lam, "sync_csv_viewport_overlays_for_screen"):
                    try:
                        lam.sync_csv_viewport_overlays_for_screen(si)
                    except Exception:
                        pass
                elif si == 1 and hasattr(lam, "_sync_csv_viewport_hud"):
                    try:
                        lam._sync_csv_viewport_hud()
                    except Exception:
                        pass
        else:
            # feature OFF 먼저 — toggle bounce 와 무관하게 패널 clear
            set_viewport_floorplan_panel_feature_rt(False)
            try:
                set_toggle_floorplan_panel(False)
            except Exception:
                pass
            force_remove_floorplan_panels()
            lam = _lam_window(ext)
            by_fp = getattr(lam, "_floorplan_panel_by_screen", None) if lam else None
            if isinstance(by_fp, dict):
                for panel in list(by_fp.values()):
                    try:
                        panel.destroy()
                    except Exception:
                        pass
                by_fp.clear()
    except Exception:
        pass


def _on_viewport_floorplan_visibility_changed(ext: Any) -> None:
    _apply_viewport_floorplan_visibility(ext)


def _apply_viewport_status_visibility(ext: Any) -> None:
    mdl = getattr(ext, "_ui_show_viewport_status_model", None)
    if mdl is None:
        return
    try:
        visible = bool(mdl.as_bool)
    except Exception:
        visible = False
    lam = _lam_window(ext)
    if lam is None:
        return
    try:
        lam._show_viewport_status_panel_rt = bool(visible)
    except Exception:
        pass
    if not visible:
        by_screen = getattr(lam, "_status_panel_by_screen", None)
        if isinstance(by_screen, dict):
            for panel in list(by_screen.values()):
                try:
                    panel.destroy()
                except Exception:
                    pass
            by_screen.clear()
        return
    for si in _csv_play_screen_indices(ext):
        csv_win = getattr(lam, "_csv_sim_windows", {}).get(si)
        if csv_win is None:
            continue
        if hasattr(lam, "_sync_status_panel_for_screen"):
            try:
                lam._sync_status_panel_for_screen(si, csv_win)
            except Exception:
                pass


def _on_viewport_status_visibility_changed(ext: Any) -> None:
    _apply_viewport_status_visibility(ext)


def _sync_federation_test_visibility(
    ext: Any, *, create_if_needed: bool = True
) -> None:
    mdl = getattr(ext, "_ui_show_federation_test_model", None)
    if mdl is None:
        return
    try:
        visible = bool(mdl.as_bool)
    except Exception:
        visible = False
    lam = _lam_window(ext)
    if lam is None:
        return
    if visible:
        if create_if_needed or getattr(lam, "_federation_test_window", None) is not None:
            try:
                if hasattr(lam, "_open_federation_test"):
                    lam._open_federation_test()
                else:
                    for win in _resolve_aux_kit_windows(ext, "federation_test"):
                        _set_kit_window_visible(win, True)
            except Exception:
                pass
        return
    for win in _resolve_aux_kit_windows(ext, "federation_test"):
        _set_kit_window_visible(win, False)


def _on_federation_test_visibility_changed(ext: Any) -> None:
    _sync_federation_test_visibility(ext, create_if_needed=True)


def mount_aux_kit_window_checkboxes_ui(
    ext: Any,
    ui: Any,
    *,
    label_width: int = 0,
    row_height: int = 22,
) -> None:
    """Viewport CSV HUD 하단 — 창 표시 체크박스."""
    if ext is None:
        return
    init_lam_aux_kit_window_models(ext)
    ui.Label(
        "창 표시",
        height=16,
        style={"color": 0xFF9AA4B2, "font_size": 11},
    )
    for model_attr, label, _which in _AUX_KIT_WINDOW_SPECS:
        mdl = getattr(ext, model_attr, None)
        if mdl is None:
            continue
        with ui.HStack(spacing=4, height=row_height):
            ui.CheckBox(model=mdl, width=20, height=row_height, style=_CHECKBOX_STYLE)
            ui.Label(label, width=label_width if label_width > 0 else 0)
    for si in _csv_play_screen_indices(ext):
        mdl = ensure_csv_play_screen_model(ext, si)
        if mdl is None:
            continue
        label = (
            "CSV 시뮬 재생"
            if len(_csv_play_screen_indices(ext)) <= 1
            else f"CSV 시뮬 재생 — 화면{si}"
        )
        with ui.HStack(spacing=4, height=row_height):
            ui.CheckBox(model=mdl, width=20, height=row_height, style=_CHECKBOX_STYLE)
            ui.Label(label, width=label_width if label_width > 0 else 0)
    for model_attr, label in _AUX_VIEWPORT_PANEL_SPECS:
        mdl = getattr(ext, model_attr, None)
        if mdl is None:
            continue
        with ui.HStack(spacing=4, height=row_height):
            ui.CheckBox(model=mdl, width=20, height=row_height, style=_CHECKBOX_STYLE)
            ui.Label(label, width=label_width if label_width > 0 else 0)


__all__ = [
    "ensure_csv_play_screen_model",
    "init_lam_aux_kit_window_models",
    "mount_aux_kit_window_checkboxes_ui",
    "sync_aux_kit_window_visibility",
]

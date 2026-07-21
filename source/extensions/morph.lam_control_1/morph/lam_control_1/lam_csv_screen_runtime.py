"""CSV 시뮬 재생 — 화면별 런타임·설정·뷰포트 효과 (TBS ``SplitScreenRuntime`` / per-screen start 패턴).

화면1: 기본 USD context + 전역 HUD 오버레이.
화면2+: 보조 context + 해당 타일 viewport 만 — **화면1 전역 API 호출 금지**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

_PRINT_PREFIX = "[LAM/CsvScreenRT]"

CameraBindMode = Literal["top_view", "play_camera"]


@dataclass
class CsvScreenRuntime:
    """화면 1개분 CSV 재생·오버레이 바인딩 (registry/scheduler/stage/viewport)."""

    screen: int
    context_name: Optional[str]
    stage: Any
    registry: Any
    scheduler: Any
    master: Any
    viewport_window: Any
    viewport_api: Any
    csv_window: Any
    lam_window: Any


def _read_bool_model(m: Any) -> bool:
    if m is None:
        return False
    for attr in ("get_value_as_bool", "as_bool"):
        try:
            fn = getattr(m, attr, None)
            if callable(fn):
                return bool(fn())
        except Exception:
            pass
    try:
        return bool(m.get_value())
    except Exception:
        return False


def capture_csv_overlay_settings(csv_window: Any) -> Dict[str, bool]:
    """CSV 재생창 체크박스 → 화면별 설정 스냅샷 (TBS ``capture_case_sim_settings`` 유사)."""
    if csv_window is None:
        return {}
    read = getattr(csv_window, "_read_bool_model", _read_bool_model)
    return {
        "wafer_labels": read(getattr(csv_window, "_wafer_label_show_model", None)),
        "foup_status": read(getattr(csv_window, "_foup_status_show_model", None)),
        "device_labels": read(getattr(csv_window, "_device_labels_show_model", None)),
        "pick_whitelist": read(getattr(csv_window, "_pick_whitelist_model", None)),
        "play_prim_hide": read(getattr(csv_window, "_play_prim_hide_model", None)),
        "play_camera_fly": read(getattr(csv_window, "_play_camera_fly_model", None)),
        "top_view": read(getattr(csv_window, "_top_view_model", None)),
    }


def _resolve_aux_tile_for_screen(ext: Any, screen: int) -> tuple[Any, Any, Any, Optional[str]]:
    """화면2+ 타일 — (viewport_window/hud_mount, viewport_api, stage, win_name).

    Widget·Dock 공통. Dock 는 ``_lam_multi_viewport_entries`` + ``LAM_SimSplit_*``.
    """
    si = max(2, int(screen))
    wname = None
    vp_api = None
    vw = None
    st = None
    try:
        from .lam_multi_viewport import (
            _resolve_viewport_api_for_workspace_name,
            _resolve_viewport_window_for_workspace_name,
            _viewport_window_name_for_screen,
        )
        from .lam_multi_viewport_widget import (
            get_split_hud_mount,
            get_split_viewport_api,
            is_split_widget_layout_active,
        )

        wname = _viewport_window_name_for_screen(si)
        if is_split_widget_layout_active(ext):
            vp_api = get_split_viewport_api(ext, wname)
            mount = get_split_hud_mount(ext, wname)
            if mount is not None:
                vw = mount
                if vp_api is None:
                    vp_api = getattr(mount, "viewport_api", None)
            tiles = getattr(ext, "_lam_split_widget_tiles", None)
            if isinstance(tiles, dict):
                rec = tiles.get(str(wname))
                if isinstance(rec, dict):
                    if vw is None:
                        vw = rec.get("viewport_window") or rec.get("hud_mount")
                    if vp_api is None:
                        vp_api = rec.get("api")
                    ctx_key = str(rec.get("context_name") or "").strip()
                    if ctx_key:
                        try:
                            import omni.usd as ou

                            ctx = ou.get_context(ctx_key)
                            if ctx is not None:
                                st = ctx.get_stage()
                        except Exception:
                            pass
        else:
            # Dock: entries → Workspace 이름 → viewport_api
            vw = _resolve_viewport_window_for_workspace_name(wname)
            vp_api = _resolve_viewport_api_for_workspace_name(wname)
            if vp_api is None and vw is not None:
                vp_api = getattr(vw, "viewport_api", None)
            entries = list(getattr(ext, "_lam_multi_viewport_entries", None) or [])
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                if str(ent.get("win_name") or "") != str(wname):
                    continue
                ctx_key = str(ent.get("context_name") or "").strip()
                if ctx_key and st is None:
                    try:
                        import omni.usd as ou

                        ctx = ou.get_context(ctx_key)
                        if ctx is not None:
                            st = ctx.get_stage()
                    except Exception:
                        pass
                if vw is None:
                    for key in ("viewport_window", "window", "kit_vp"):
                        cand = ent.get(key)
                        if cand is None:
                            continue
                        if callable(getattr(cand, "get_frame", None)):
                            vw = cand
                            break
                        inner = getattr(cand, "viewport_api", None)
                        if inner is not None:
                            vw = cand
                            if vp_api is None:
                                vp_api = inner
                            break
                if vp_api is None and vw is not None:
                    vp_api = getattr(vw, "viewport_api", None)
                break
    except Exception:
        pass
    return vw, vp_api, st, wname


def _ensure_viewport_api_context(vp_api: Any, context_name: Optional[str]) -> None:
    ctx_key = str(context_name or "").strip()
    if not ctx_key or vp_api is None:
        return
    for attr in ("usd_context_name", "context_name"):
        try:
            if hasattr(vp_api, attr):
                setattr(vp_api, attr, ctx_key)
        except Exception:
            pass


def resolve_csv_screen_runtime(
    lam_window: Any,
    screen: int,
    *,
    csv_window: Any = None,
    require_aux: bool = False,
) -> Optional[CsvScreenRuntime]:
    """화면별 registry/scheduler/stage/viewport 를 한 번에 조회."""
    si = max(1, int(screen))
    if lam_window is None:
        return None
    ext = getattr(lam_window, "_kit_ext", None)
    if csv_window is None:
        csv_window = getattr(lam_window, "_csv_sim_windows", {}).get(si)
    try:
        from .lam_csv_play_screen import (
            get_registry_scheduler_for_lam_screen,
            get_stage_for_screen,
            resolve_viewport_api_for_screen,
            resolve_viewport_window_for_screen,
            usd_context_name_for_screen,
        )

        reg, sch = get_registry_scheduler_for_lam_screen(lam_window, si, allow_fallback=False)
        cn = usd_context_name_for_screen(ext, si) if ext is not None else None
        st = get_stage_for_screen(ext, si) if ext is not None else None
        if st is None and si <= 1:
            try:
                from .lam_prim_utils import get_stage

                st = get_stage()
            except Exception:
                st = None
        main_vp = getattr(lam_window, "_viewport", None)
        vw = None
        vp_api = None
        if si > 1 and ext is not None:
            tile_vw, tile_api, tile_st, _wn = _resolve_aux_tile_for_screen(ext, si)
            if tile_vw is not None:
                vw = tile_vw
            if tile_api is not None:
                vp_api = tile_api
            if tile_st is not None:
                st = tile_st
        if vw is None and ext is not None:
            vw = resolve_viewport_window_for_screen(ext, si, main_viewport=main_vp)
        if vp_api is None and ext is not None:
            vp_api = resolve_viewport_api_for_screen(ext, si)
        if vp_api is None and vw is not None:
            vp_api = getattr(vw, "viewport_api", None)
        if vw is None and si <= 1 and main_vp is not None:
            vw = getattr(main_vp, "_dedicated_window", None)
        if vp_api is None and vw is not None:
            vp_api = getattr(vw, "viewport_api", None)
        if vp_api is not None and cn:
            _ensure_viewport_api_context(vp_api, cn)
        master = getattr(lam_window, "_master", None)
        if si > 1 and ext is not None:
            try:
                from .lam_split_composed_loader import get_split_runtime_for_screen

                rt = get_split_runtime_for_screen(ext, si)
                if rt is not None:
                    if getattr(rt, "master", None) is not None:
                        master = rt.master
                    if getattr(rt, "registry", None) is not None:
                        reg = rt.registry
                    if getattr(rt, "scheduler", None) is not None:
                        sch = rt.scheduler
                    if getattr(rt, "context_name", None):
                        cn = rt.context_name
            except Exception:
                pass
    except Exception as exc:
        print(f"{_PRINT_PREFIX} resolve screen={si} failed: {exc}", flush=True)
        return None

    if require_aux and si > 1:
        # play 는 stage+registry 필수. viewport_api 는 camera/overlay 용.
        if cn is None or reg is None or sch is None or st is None:
            print(
                f"{_PRINT_PREFIX} screen{si} runtime incomplete — "
                f"ctx={cn!r} vp={vp_api is not None} reg={reg is not None} "
                f"sch={sch is not None} stage={st is not None}",
                flush=True,
            )
            return None
        if vp_api is None:
            print(
                f"{_PRINT_PREFIX} screen{si} viewport_api 미준비 — "
                "play는 진행, camera/overlay 는 재동기화 대기 "
                f"ctx={cn!r} vw={vw is not None}",
                flush=True,
            )

    return CsvScreenRuntime(
        screen=si,
        context_name=cn,
        stage=st,
        registry=reg,
        scheduler=sch,
        master=master,
        viewport_window=vw,
        viewport_api=vp_api,
        csv_window=csv_window,
        lam_window=lam_window,
    )


def bind_viewport_camera_for_screen(
    runtime: CsvScreenRuntime,
    mode: CameraBindMode,
) -> bool:
    """화면 N viewport 에 play/top 카메라 적용 (Perspective 또는 Camera prim)."""
    vp_api = runtime.viewport_api
    if vp_api is None:
        return False
    ctx = str(runtime.context_name or "").strip() or None
    if runtime.screen > 1 and not ctx:
        print(
            f"{_PRINT_PREFIX} screen{runtime.screen} camera bind skip — no USD context",
            flush=True,
        )
        return False
    from .lam_play_camera_fly import (
        _PERSP_CAMERA_PATH,
        _finish_fly_to_target,
        apply_camera_view,
        apply_play_camera_prim_view_spec,
        apply_top_view_camera_prim_view_spec,
        apply_view_to_camera_prim,
        camera_fly_usd_context,
        ensure_camera_prim_baseline,
        get_play_camera_target_snapshot,
        get_session_fly_up_xyz,
        get_top_view_target_snapshot,
        play_assign_prim_path,
        play_camera_prim_path,
        play_camera_target_configured,
        play_camera_use_preset_coords,
        restore_perspective_on_viewport,
        set_viewport_camera_prim_path_on_api,
        top_view_assign_prim_path,
        top_view_camera_prim_path,
        top_view_target_configured,
        top_view_use_preset_coords,
    )

    if mode == "top_view":
        if not top_view_target_configured():
            return False
        use_preset = top_view_use_preset_coords()
        prim_path = top_view_assign_prim_path() or top_view_camera_prim_path()
        snap = get_top_view_target_snapshot()
        up = get_session_fly_up_xyz(top_view=True)
    else:
        if not play_camera_target_configured():
            return False
        use_preset = play_camera_use_preset_coords()
        prim_path = play_assign_prim_path() or play_camera_prim_path()
        snap = get_play_camera_target_snapshot()
        up = get_session_fly_up_xyz(play=True)

    _ensure_viewport_api_context(vp_api, ctx)
    ctx_s = str(ctx or "")
    ok = False
    with camera_fly_usd_context(ctx):
        # Camera prim 모드: 화면1 apply_top_view_target / play finish 와 동일 —
        # prim 스펙 적용 후 해당 타일 viewport 만 bind (Persp 스냅 불필요).
        if prim_path and not use_preset:
            if mode == "top_view":
                apply_top_view_camera_prim_view_spec()
            else:
                apply_play_camera_prim_view_spec()
            ensure_camera_prim_baseline(prim_path)
            if snap is None:
                snap = (
                    get_top_view_target_snapshot()
                    if mode == "top_view"
                    else get_play_camera_target_snapshot()
                )
            if snap is not None:
                ok = bool(
                    _finish_fly_to_target(
                        snap,
                        up_xyz=up,
                        assign_prim_path=prim_path,
                        log_context=f"screen{runtime.screen}_{mode}",
                        viewport_api=vp_api,
                        usd_context_name=ctx_s,
                    )
                )
        else:
            # Preset 모드: 해당 context Persp 에 목표 좌표 적용
            restore_perspective_on_viewport(vp_api, ctx_s)
            if snap is not None:
                ok = bool(
                    apply_camera_view(
                        snap,
                        up_xyz=up,
                        camera_path=_PERSP_CAMERA_PATH,
                    )
                )
            if prim_path and snap is not None:
                apply_view_to_camera_prim(prim_path, snap, up_xyz=up)
                ensure_camera_prim_baseline(prim_path)
                resolved_path = prim_path
                if runtime.stage is not None:
                    try:
                        from .lam_multi_viewport_widget import _resolve_camera_path_for_stage

                        got = _resolve_camera_path_for_stage(runtime.stage, prim_path)
                        if got is not None:
                            resolved_path = str(got)
                    except Exception:
                        pass
                if set_viewport_camera_prim_path_on_api(
                    vp_api,
                    resolved_path,
                    usd_context_name=ctx_s,
                ):
                    ok = True
            else:
                if set_viewport_camera_prim_path_on_api(
                    vp_api,
                    _PERSP_CAMERA_PATH,
                    usd_context_name=ctx_s,
                ):
                    ok = True or ok

    if ok:
        print(
            f"{_PRINT_PREFIX} screen{runtime.screen} viewport camera bind "
            f"mode={mode} prim={(prim_path or _PERSP_CAMERA_PATH)!r}",
            flush=True,
        )
    else:
        print(
            f"{_PRINT_PREFIX} screen{runtime.screen} viewport camera bind FAILED mode={mode}",
            flush=True,
        )
    return ok


def apply_prim_hide_for_screen(runtime: CsvScreenRuntime, *, enabled: bool) -> None:
    phase = "ui_hide" if enabled else "ui_show"
    if runtime.screen <= 1:
        from .lam_play_prim_hide import apply_play_prim_hide_ui_instant

        apply_play_prim_hide_ui_instant(phase)
        return
    cn = runtime.context_name
    if not cn:
        return
    from .lam_play_prim_hide import apply_play_prim_hide_ui_instant_for_context

    apply_play_prim_hide_ui_instant_for_context(
        cn,
        phase,
        prim_hide_checked=bool(enabled),
    )


def restore_play_stop_perspective_for_screen(runtime: CsvScreenRuntime) -> None:
    """시뮬 정지 후 Perspective·줌 복귀 — 화면1·2 동일 규칙, 타일만 분리."""
    if runtime.screen <= 1:
        from .lam_play_camera_fly import restore_perspective_after_play_camera_mode

        restore_perspective_after_play_camera_mode()
        return
    vp_api = runtime.viewport_api
    ctx = str(runtime.context_name or "").strip()
    if vp_api is None:
        return
    from .lam_play_camera_fly import restore_perspective_after_play_stop_for_viewport

    restore_perspective_after_play_stop_for_viewport(vp_api, ctx)


def schedule_play_stop_perspective_restore_for_screen(runtime: CsvScreenRuntime) -> None:
    """정지 클릭 직후 race 대비 — 화면별 Perspective 복귀 예약."""
    if runtime.screen <= 1:
        from .lam_play_camera_fly import schedule_restore_perspective_after_play_stop

        schedule_restore_perspective_after_play_stop(delay_frames=0)
        return
    vp_api = runtime.viewport_api
    ctx = str(runtime.context_name or "").strip()
    if vp_api is None:
        return
    from .lam_play_camera_fly import (
        schedule_restore_perspective_after_play_stop_for_viewport,
    )

    schedule_restore_perspective_after_play_stop_for_viewport(
        vp_api,
        ctx,
        delay_frames=0,
    )


def sync_play_prim_hide_checkbox_after_play_start(
    *,
    screen: int,
    csv_window: Any = None,
) -> None:
    """Play 시작 자동 숨김 완료 후 「prim숨김」 체크만 ON (visibility 재적용 없음)."""
    si = max(1, int(screen))
    if si <= 1:
        from .lam_viewport_overlay_state import set_toggle_play_prim_hide

        set_toggle_play_prim_hide(True, apply_side_effect=False)
        return
    if csv_window is None:
        return
    sync_fn = getattr(csv_window, "sync_play_prim_hide_checkbox_ui", None)
    if callable(sync_fn):
        sync_fn(True)
        return
    m = getattr(csv_window, "_play_prim_hide_model", None)
    if m is None:
        return
    try:
        m.set_value(True)
    except Exception:
        pass


def apply_top_view_for_screen(runtime: CsvScreenRuntime, *, enabled: bool) -> bool:
    if runtime.screen <= 1:
        from .lam_viewport_overlay_state import set_toggle_top_view

        set_toggle_top_view(bool(enabled), from_ui_model=False)
        return True
    vp_api = runtime.viewport_api
    if vp_api is None and runtime.lam_window is not None:
        # 타일 생성 직후 runtime 스냅샷이 비어 있을 수 있음 — 재조회
        refreshed = resolve_csv_screen_runtime(
            runtime.lam_window,
            runtime.screen,
            csv_window=runtime.csv_window,
            require_aux=False,
        )
        if refreshed is not None and refreshed.viewport_api is not None:
            runtime.viewport_api = refreshed.viewport_api
            runtime.viewport_window = refreshed.viewport_window
            if refreshed.context_name:
                runtime.context_name = refreshed.context_name
            if refreshed.stage is not None:
                runtime.stage = refreshed.stage
            vp_api = refreshed.viewport_api
    if vp_api is None:
        print(
            f"{_PRINT_PREFIX} screen{runtime.screen} top view skip — viewport_api 없음",
            flush=True,
        )
        return False
    ctx = str(runtime.context_name or "").strip()
    from .lam_viewport_top_view import set_viewport_top_view_navigation_locked

    if enabled:
        ok = bind_viewport_camera_for_screen(runtime, "top_view")
        if ok:
            set_viewport_top_view_navigation_locked(vp_api, True)
            print(
                f"{_PRINT_PREFIX} screen{runtime.screen} top view ON + nav lock",
                flush=True,
            )
        else:
            print(
                f"{_PRINT_PREFIX} screen{runtime.screen} top view bind 실패",
                flush=True,
            )
        return ok
    from .lam_play_camera_fly import restore_perspective_on_viewport

    restore_perspective_on_viewport(vp_api, ctx)
    set_viewport_top_view_navigation_locked(vp_api, False)
    print(
        f"{_PRINT_PREFIX} screen{runtime.screen} top view OFF + Perspective",
        flush=True,
    )
    return True


def sync_csv_screen_3d_overlays(runtime: CsvScreenRuntime) -> None:
    """FOUP·디바이스·웨이퍼 3D 오버레이 — 화면 N 타일 viewport 에 mount."""
    lam = runtime.lam_window
    si = runtime.screen
    csv_win = runtime.csv_window
    if lam is None or csv_win is None:
        return
    try:
        from .lam_viewport_foup_status_3d import LamFoupStatus3dPanel
        from .lam_viewport_device_labels_3d import LamViewportDeviceLabels3d
        from .lam_wafer_viewport_labels import LamWaferFoupViewportLabels
    except Exception as exc:
        print(f"{_PRINT_PREFIX} overlay import failed: {exc}", flush=True)
        return

    tile_vw = runtime.viewport_window
    if si > 1 and tile_vw is None:
        print(
            f"{_PRINT_PREFIX} screen{si} 3D overlay — viewport_window 없음 "
            "(Dock LAM_SimSplit / Widget hud_mount 대기)",
            flush=True,
        )

    by_foup = getattr(lam, "_foup_status_3d_by_screen", None)
    if not isinstance(by_foup, dict):
        by_foup = {}
        lam._foup_status_3d_by_screen = by_foup
    foup = by_foup.get(si)
    if foup is None:
        foup = LamFoupStatus3dPanel(
            csv_win,
            viewport=getattr(lam, "_viewport", None),
            screen=si,
        )
        by_foup[si] = foup
    # 화면2+: runtime 타일만 신뢰. None 이면 잘못된 메인 Viewport 캐시 제거.
    if si > 1:
        foup._viewport_window = tile_vw
    elif tile_vw is not None:
        foup._viewport_window = tile_vw
    try:
        foup.sync_layers(delay_frames=0 if tile_vw is not None else 12)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} foup sync: {exc}", flush=True)

    by_dev = getattr(lam, "_device_labels_3d_by_screen", None)
    if not isinstance(by_dev, dict):
        by_dev = {}
        lam._device_labels_3d_by_screen = by_dev
    dev = by_dev.get(si)
    if dev is None:
        dev = LamViewportDeviceLabels3d(
            viewport=getattr(lam, "_viewport", None),
            screen=si,
            csv_window=csv_win,
        )
        by_dev[si] = dev
    if si > 1:
        try:
            setattr(dev, "_viewport_window", tile_vw)
        except Exception:
            pass
    elif tile_vw is not None:
        try:
            setattr(dev, "_viewport_window", tile_vw)
        except Exception:
            pass
    try:
        dev.sync_layers(delay_frames=0 if tile_vw is not None else 12)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} device labels sync: {exc}", flush=True)

    by_wafer = getattr(lam, "_wafer_foup_labels_by_screen", None)
    if not isinstance(by_wafer, dict):
        by_wafer = {}
        lam._wafer_foup_labels_by_screen = by_wafer
    wafer = by_wafer.get(si)
    ext = getattr(lam, "_kit_ext", None)
    if wafer is None:
        wafer = LamWaferFoupViewportLabels(
            viewport=getattr(lam, "_viewport", None),
            master=runtime.master,
            ext_id=f"screen{si}",
            screen=si,
            csv_window=csv_win,
            kit_ext=ext,
        )
        by_wafer[si] = wafer
    else:
        wafer._csv_window = csv_win
        if runtime.master is not None:
            wafer._master = runtime.master
    if si > 1:
        try:
            setattr(wafer, "_viewport_window", tile_vw)
        except Exception:
            pass
    elif tile_vw is not None:
        try:
            setattr(wafer, "_viewport_window", tile_vw)
        except Exception:
            pass
    try:
        wafer.sync_layers(delay_frames=0 if tile_vw is not None else 12)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} wafer labels sync: {exc}", flush=True)


def apply_csv_screen_viewport_effects(runtime: CsvScreenRuntime) -> None:
    """체크박스 스냅샷 → 해당 화면 prim 숨김·탑뷰만 적용 (3D 라벨은 별도 sync).

    탑뷰는 fly 체크 여부와 무관하게 항상 반영한다 (재생 중 토글 포함).
    Play 시작 시 fly 와의 순서는 표시 전환 ``on_complete`` → preflight 가 보장한다.
    """
    settings = capture_csv_overlay_settings(runtime.csv_window)
    apply_prim_hide_for_screen(runtime, enabled=bool(settings.get("play_prim_hide")))
    apply_top_view_for_screen(runtime, enabled=bool(settings.get("top_view")))


def sync_csv_screen_overlays(lam_window: Any, screen: int) -> None:
    """화면별 CSV 창 설정 → 해당 화면 viewport/USD 만 동기화 (진입점)."""
    si = max(1, int(screen))
    if si <= 1:
        # 화면1: HUD(foup/device/status) + 웨이퍼 번호(별도 SceneView).
        # 예전엔 HUD 만 하고 return → 화면1 「웨이퍼번호보기」 체크해도 안 나옴.
        if hasattr(lam_window, "_sync_csv_viewport_hud"):
            lam_window._sync_csv_viewport_hud()
        if hasattr(lam_window, "_sync_wafer_foup_viewport_labels_only"):
            try:
                lam_window._sync_wafer_foup_viewport_labels_only(delay_frames=0)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} screen1 wafer label sync: {exc}",
                    flush=True,
                )
        return
    csv_win = getattr(lam_window, "_csv_sim_windows", {}).get(si)
    if csv_win is None and hasattr(lam_window, "_ensure_csv_sim_play_window"):
        try:
            csv_win = lam_window._ensure_csv_sim_play_window(si)
        except Exception:
            return
    if csv_win is not None:
        try:
            csv_win.ensure_playback_models()
        except Exception:
            pass
    runtime = resolve_csv_screen_runtime(
        lam_window,
        si,
        csv_window=csv_win,
        require_aux=False,
    )
    if runtime is None:
        print(f"{_PRINT_PREFIX} screen{si} overlay sync — runtime 없음", flush=True)
        return
    if csv_win is not None:
        sync_csv_screen_3d_overlays(runtime)
        if runtime.viewport_window is None:
            print(
                f"{_PRINT_PREFIX} screen{si} 3D overlay — viewport_window/hud_mount 없음 "
                "(분할 타일 deferred 생성 후 다시 체크하세요)",
                flush=True,
            )
        else:
            print(
                f"{_PRINT_PREFIX} screen{si} overlay sync "
                f"foup={capture_csv_overlay_settings(csv_win).get('foup_status')} "
                f"device={capture_csv_overlay_settings(csv_win).get('device_labels')} "
                f"top={capture_csv_overlay_settings(csv_win).get('top_view')} "
                f"vp={runtime.viewport_api is not None}",
                flush=True,
            )
    if runtime.screen > 1 and (
        not runtime.context_name
        or runtime.viewport_api is None
        or runtime.stage is None
    ):
        # 한 번 더 타일 resolve (Dock LAM_SimSplit / Widget 준비 지연)
        runtime = resolve_csv_screen_runtime(
            lam_window,
            si,
            csv_window=csv_win,
            require_aux=False,
        ) or runtime
    missing = []
    if runtime.screen > 1:
        if not runtime.context_name:
            missing.append("context")
        if runtime.viewport_api is None:
            missing.append("viewport_api")
        if runtime.stage is None:
            missing.append("stage")
        if missing:
            print(
                f"{_PRINT_PREFIX} screen{si} viewport 효과 skip — "
                f"미준비: {', '.join(missing)} (3D 라벨만 동기화)",
                flush=True,
            )
            return
    apply_csv_screen_viewport_effects(runtime)


def run_csv_screen_play_preflight(runtime: CsvScreenRuntime) -> bool:
    """Play worker — 화면2+ preflight (화면1 과 동일 타임라인·화면별 context)."""
    from .simulation_play import csv_playback_stop_requested

    si = runtime.screen
    if si <= 1:
        return True
    if csv_playback_stop_requested(screen=si):
        return False
    settings = capture_csv_overlay_settings(runtime.csv_window)
    # 탑뷰만 켜진 경우 — 카메라·prim hide 없이 탑뷰만 동기화
    if (
        not settings.get("play_camera_fly")
        and not _play_prim_hide_specs_configured()
        and settings.get("top_view")
    ):
        try:
            apply_top_view_for_screen(runtime, enabled=True)
        except Exception:
            pass
        return True
    if runtime.context_name is None or runtime.stage is None:
        print(
            f"{_PRINT_PREFIX} screen{si} preflight skip — "
            f"ctx/stage 미준비 ctx={runtime.context_name!r} stage={runtime.stage is not None}",
            flush=True,
        )
        return False
    try:
        from .lam_play_start_sequence import run_aux_screen_play_start_preflight

        ok = run_aux_screen_play_start_preflight(runtime, settings)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} screen{si} play preflight: {exc}", flush=True)
        return False
    return bool(ok) and not csv_playback_stop_requested(screen=si)


def _play_prim_hide_specs_configured() -> bool:
    try:
        from .lam_play_prim_hide import play_prim_hide_specs_configured

        return bool(play_prim_hide_specs_configured())
    except Exception:
        return False


__all__ = [
    "CsvScreenRuntime",
    "apply_csv_screen_viewport_effects",
    "apply_prim_hide_for_screen",
    "restore_play_stop_perspective_for_screen",
    "schedule_play_stop_perspective_restore_for_screen",
    "sync_play_prim_hide_checkbox_after_play_start",
    "apply_top_view_for_screen",
    "bind_viewport_camera_for_screen",
    "capture_csv_overlay_settings",
    "resolve_csv_screen_runtime",
    "run_csv_screen_play_preflight",
    "sync_csv_screen_3d_overlays",
    "sync_csv_screen_overlays",
]

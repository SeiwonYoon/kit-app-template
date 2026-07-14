"""
RenderProduct 생성 원인 조사 — **관측 전용**.

증상 완화(kick_render, viewport_changed, camera, manipulator 등)는 건드리지 않는다.
Widget / Context / Stage / Camera / ViewportScene / RenderProduct 체인이
어느 시점에 None → alive 로 바뀌는지 기록한다.

독립 ``ui.Window`` 실험으로 ViewportWindow+HStack 구조 제약 vs aux Context 문제를 분리한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import omni.kit.app as kit_app
import omni.ui as ui

_ISOLATED_TEST_WINDOW = "TBS_RP_Isolated_Test"
_ISOLATED_TEST_TAG = "[LAM/rp-isolated]"


def viewport_rp_diag_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import VIEWPORT_RP_DIAG_ENABLED

        return bool(VIEWPORT_RP_DIAG_ENABLED)
    except Exception:
        return False


def viewport_rp_isolated_test_enabled() -> bool:
    try:
        from .lam_sim_control_defaults import VIEWPORT_RP_ISOLATED_WINDOW_TEST

        return bool(VIEWPORT_RP_ISOLATED_WINDOW_TEST)
    except Exception:
        return False


def viewport_rp_timeline_frames() -> int:
    try:
        from .lam_sim_control_defaults import VIEWPORT_RP_TIMELINE_FRAMES

        return max(4, int(VIEWPORT_RP_TIMELINE_FRAMES))
    except Exception:
        return 12


def _fmt_val(val: Any, *, max_len: int = 140) -> str:
    if val is None:
        return "None"
    if isinstance(val, (bool, int, float)):
        return repr(val)
    if isinstance(val, str):
        return val if len(val) <= max_len else val[: max_len - 3] + "..."
    try:
        tname = type(val).__name__
        return f"{tname}@{id(val):#x}"
    except Exception:
        return "?"


def _safe_attr(obj: Any, attr: str) -> str:
    if obj is None:
        return "None"
    try:
        return _fmt_val(getattr(obj, attr, None))
    except Exception as exc:
        return f"err:{exc}"


def _safe_call(obj: Any, meth: str) -> str:
    if obj is None:
        return "None"
    fn = getattr(obj, meth, None)
    if not callable(fn):
        return "(no-method)"
    try:
        return _fmt_val(fn())
    except Exception as exc:
        return f"err:{exc}"


def _widget_parent_chain(widget: Any, *, depth: int = 10) -> str:
    parts: List[str] = []
    obj = widget
    for _ in range(max(1, depth)):
        if obj is None:
            break
        try:
            parts.append(f"{type(obj).__name__}@{id(obj):#x}")
        except Exception:
            parts.append("?")
        try:
            obj = getattr(obj, "parent", None)
        except Exception:
            break
    return " -> ".join(parts) if parts else "None"


def _enumerate_viewport_widgets() -> str:
    try:
        from omni.kit.widget.viewport import ViewportWidget

        inst = list(ViewportWidget.get_instances())
        if not inst:
            return "count=0"
        bits = [f"#{i}:{type(w).__name__}@{id(w):#x} ctx={_safe_attr(w, 'usd_context_name')}" for i, w in enumerate(inst)]
        return f"count={len(inst)} " + " | ".join(bits)
    except Exception as exc:
        return f"err:{exc}"


def probe_render_product_chain(
    api: Any,
    widget: Any = None,
    *,
    scene_view: Any = None,
) -> Dict[str, str]:
    """
    RenderProduct / Hydra / ViewportScene 생성 체인 스냅샷.

    공식 파이프라인 (가설):
    ViewportWidget → ViewportScene → RenderProduct → HydraTexture → Renderer → ViewportAPI
    """
    out: Dict[str, str] = {}

    # --- RenderProduct / Hydra / Renderer (ViewportAPI) ---
    rp_attrs = (
        "render_product_path",
        "hydra_engine",
        "resolution",
        "full_resolution",
        "fps",
        "camera_path",
        "projection",
        "render_mode",
        "usd_context_name",
        "id",
        "viewport_id",
        "stage",
        "usd_context",
        "hydra_texture",
        "texture",
        "renderer",
        "render_delegate",
        "render_product",
    )
    scene_attrs = (
        "scene",
        "viewport_scene",
        "scene_delegate",
        "scene_model",
        "_scene",
        "_viewport_scene",
        "_scene_delegate",
        "_scene_model",
    )

    for attr in rp_attrs:
        out[f"api.{attr}"] = _safe_attr(api, attr)
    for meth in (
        "get_render_product_path",
        "get_render_product",
        "get_hydra_texture",
        "get_texture",
        "get_renderer",
        "get_texture_resolution",
        "get_full_texture_resolution",
    ):
        out[f"api.{meth}()"] = _safe_call(api, meth)

    for attr in scene_attrs:
        out[f"api.{attr}"] = _safe_attr(api, attr)

    # --- ViewportWidget ---
    for attr in ("usd_context_name", "resolution", "fill_frame", "visible", "enabled"):
        out[f"widget.{attr}"] = _safe_attr(widget, attr)
    for attr in scene_attrs:
        out[f"widget.{attr}"] = _safe_attr(widget, attr)
    out["widget.parent_chain"] = _widget_parent_chain(widget)
    out["widget.viewport_api"] = _safe_attr(widget, "viewport_api")

    # --- SceneView (우리가 붙이는 omni.ui.scene.SceneView — manipulator용) ---
    out["scene_view"] = _fmt_val(scene_view)
    out["scene_view.camera_model"] = _safe_attr(scene_view, "camera_model")
    out["scene_view.model"] = _safe_attr(scene_view, "model")

    # --- Kit 전역 ViewportWidget 인스턴스 ---
    out["ViewportWidget.get_instances"] = _enumerate_viewport_widgets()

    # --- RenderProduct 존재 (단일 bool) ---
    rp_path = out.get("api.get_render_product_path()") or out.get("api.render_product_path")
    has_rp = bool(
        rp_path
        and str(rp_path) not in ("None", "(no-method)", "")
        and not str(rp_path).startswith("err:")
    )
    out["HAS_RENDER_PRODUCT"] = "True" if has_rp else "False"

    return out


def log_rp_investigation(
    phase: str,
    label: str,
    api: Any,
    widget: Any = None,
    *,
    scene_view: Any = None,
    extra: str = "",
) -> Dict[str, str]:
    """구조화된 다줄 로그 — ``[LAM/rp-invest]`` 접두사."""
    if not viewport_rp_diag_enabled():
        return {}
    probe = probe_render_product_chain(api, widget, scene_view=scene_view)
    try:
        print(f"[LAM/rp-invest] ===== phase={phase!r} label={label!r} =====", flush=True)
        print(
            f"[LAM/rp-invest] HAS_RENDER_PRODUCT={probe.get('HAS_RENDER_PRODUCT', '?')} "
            f"parent={probe.get('widget.parent_chain', '?')}",
            flush=True,
        )
        groups = (
            ("RenderProduct", ("api.render_product_path", "api.get_render_product_path()", "api.get_render_product()", "api.render_product", "HAS_RENDER_PRODUCT")),
            ("Hydra/Renderer", ("api.hydra_engine", "api.render_mode", "api.renderer", "api.render_delegate", "api.get_renderer()", "api.get_hydra_texture()", "api.hydra_texture", "api.texture")),
            ("Resolution/FPS", ("api.resolution", "api.full_resolution", "api.get_texture_resolution()", "api.get_full_texture_resolution()", "api.fps", "widget.resolution")),
            ("Context/Stage", ("api.usd_context_name", "api.usd_context", "api.stage", "api.camera_path", "api.projection", "widget.usd_context_name")),
            ("ViewportId", ("api.id", "api.viewport_id")),
            ("ViewportScene", tuple(k for k in probe if "scene" in k or "delegate" in k or "model" in k)),
            ("WidgetInstances", ("ViewportWidget.get_instances", "widget.viewport_api", "widget.parent_chain", "scene_view")),
        )
        for group_name, keys in groups:
            lines = []
            for key in keys:
                if key in probe:
                    lines.append(f"  {key}={probe[key]}")
            if lines:
                print(f"[LAM/rp-invest] -- {group_name} --", flush=True)
                for line in lines:
                    print(f"[LAM/rp-invest]{line}", flush=True)
        if extra:
            print(f"[LAM/rp-invest] extra: {extra}", flush=True)
        print(f"[LAM/rp-invest] ===== end {phase!r} =====", flush=True)
    except Exception:
        pass
    return probe


async def observe_rp_timeline(
    ext: Any,
    api: Any,
    widget: Any,
    label: str,
    *,
    token: int = 0,
    frames: Optional[int] = None,
    scene_view: Any = None,
) -> None:
    """
    N 프레임 동안 RenderProduct/Hydra 변화만 관측 (API 호출·수정 없음).
    """
    if not viewport_rp_diag_enabled():
        return
    n_frames = frames if frames is not None else viewport_rp_timeline_frames()
    prev_rp: Optional[str] = None
    try:
        print(
            f"[LAM/rp-timeline] START label={label!r} frames={n_frames}",
            flush=True,
        )
    except Exception:
        pass
    for frame_i in range(n_frames):
        if int(getattr(ext, "_lam_multi_viewport_apply_token", 0) or 0) != int(token):
            return
        await kit_app.get_app().next_update_async()
        probe = probe_render_product_chain(api, widget, scene_view=scene_view)
        rp = probe.get("api.get_render_product_path()") or probe.get("api.render_product_path")
        hydra = probe.get("api.hydra_engine")
        res = probe.get("api.resolution")
        fps = probe.get("api.fps")
        changed = rp != prev_rp
        prev_rp = rp
        try:
            print(
                f"[LAM/rp-timeline] frame={frame_i + 1}/{n_frames} label={label!r} "
                f"RP={rp} hydra={hydra} res={res} fps={fps}"
                f"{' *** RP_CHANGED ***' if changed and frame_i > 0 else ''}",
                flush=True,
            )
        except Exception:
            pass
    try:
        print(f"[LAM/rp-timeline] END label={label!r}", flush=True)
    except Exception:
        pass


async def run_isolated_viewport_widget_test(
    ext: Any,
    aux_ctx: str,
    half_w: int,
    th: int,
    *,
    token: int = 0,
    hd_engine: Any = None,
    hd_engine_mode: str = "omitted",
) -> Dict[str, str]:
    """
    CASE A/B 실험: ViewportWindow+HStack 밖 독립 ``ui.Window`` 에 aux ViewportWidget 1개.

    - CASE 1: 여기서 RP 생성 O, embedded 실패 → ViewportWindow 내부 다중 Widget 제약
    - CASE 2: 여기서도 RP 생성 X → aux Context / Stage / 생성 순서 문제
    """
    result: Dict[str, str] = {"case": "not-run"}
    if not viewport_rp_isolated_test_enabled():
        return result

    ctx_key = str(aux_ctx or "").strip()
    if not ctx_key:
        result["case"] = "skip-no-ctx"
        return result

    try:
        from omni.kit.widget.viewport import ViewportWidget
    except Exception as exc:
        result["case"] = f"import-fail:{exc}"
        return result

    tile_w = max(64, int(half_w) or 640)
    tile_h = max(64, int(th) or 480)
    cam = "/OmniverseKit_Persp"

    # 기존 테스트 창 정리
    try:
        old = ui.Workspace.get_window(_ISOLATED_TEST_WINDOW)
        if old is not None:
            old.visible = False
            old.destroy()
    except Exception:
        pass

    vw_kw: Dict[str, Any] = {
        "usd_context_name": ctx_key,
        "camera_path": cam,
        "resolution": (tile_w, tile_h),
    }
    if hd_engine is not None:
        vw_kw["hd_engine"] = hd_engine

    win: Any = None
    vw: Any = None
    api: Any = None

    try:
        print(
            f"{_ISOLATED_TEST_TAG} START ctx={ctx_key!r} "
            f"hd_engine={hd_engine_mode!r} res=({tile_w},{tile_h})",
            flush=True,
        )
    except Exception:
        pass

    try:
        win = ui.Window(
            _ISOLATED_TEST_WINDOW,
            width=tile_w + 16,
            height=tile_h + 40,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        win.title = "TBS RenderProduct Isolated Test (aux)"
        with win.frame:
            vw = ViewportWidget(**vw_kw)
        try:
            vw.fill_frame = False
            vw.set_resolution((tile_w, tile_h))
        except Exception:
            pass
        api = getattr(vw, "viewport_api", None)
        win.visible = True

        log_rp_investigation(
            "isolated-create+0",
            f"isolated:{ctx_key}",
            api,
            vw,
            extra=f"hd_engine={hd_engine_mode}",
        )

        n_frames = viewport_rp_timeline_frames()
        for frame_i in range(n_frames):
            if int(getattr(ext, "_lam_multi_viewport_apply_token", 0) or 0) != int(token):
                break
            await kit_app.get_app().next_update_async()
            if api is None:
                api = getattr(vw, "viewport_api", None)
            probe = probe_render_product_chain(api, vw)
            rp = probe.get("HAS_RENDER_PRODUCT")
            try:
                print(
                    f"{_ISOLATED_TEST_TAG} frame={frame_i + 1} "
                    f"HAS_RP={rp} path={probe.get('api.get_render_product_path()')} "
                    f"hydra={probe.get('api.hydra_engine')} res={probe.get('api.resolution')}",
                    flush=True,
                )
            except Exception:
                pass

        final = probe_render_product_chain(api, vw)
        result = {
            "case": "isolated-done",
            "HAS_RENDER_PRODUCT": final.get("HAS_RENDER_PRODUCT", "?"),
            "render_product_path": final.get("api.get_render_product_path()") or final.get("api.render_product_path", "?"),
            "hydra_engine": final.get("api.hydra_engine", "?"),
            "resolution": final.get("api.resolution", "?"),
            "fps": final.get("api.fps", "?"),
            "viewport_id": final.get("api.id") or final.get("api.viewport_id", "?"),
            "hd_engine_mode": hd_engine_mode,
        }
        try:
            print(
                f"{_ISOLATED_TEST_TAG} RESULT HAS_RP={result['HAS_RENDER_PRODUCT']} "
                f"path={result['render_product_path']} hydra={result['hydra_engine']} "
                f"res={result['resolution']} fps={result['fps']} "
                f"viewport_id={result['viewport_id']}",
                flush=True,
            )
            if result.get("HAS_RENDER_PRODUCT") == "True":
                print(
                    f"{_ISOLATED_TEST_TAG} INTERPRETATION: CASE-1-candidate — "
                    "독립 Window에서는 RP 생성됨 → ViewportWindow+HStack 구조 제약 의심",
                    flush=True,
                )
            else:
                print(
                    f"{_ISOLATED_TEST_TAG} INTERPRETATION: CASE-2-candidate — "
                    "독립 Window에서도 RP 없음 → aux Context/Stage/생성순서 의심",
                    flush=True,
                )
        except Exception:
            pass
    except Exception as exc:
        result = {"case": f"fail:{exc}"}
        try:
            print(f"{_ISOLATED_TEST_TAG} FAIL {exc}", flush=True)
        except Exception:
            pass

    # 테스트 창은 결과 확인용으로 잠시 유지 (자동 destroy 안 함)
    try:
        ext._lam_rp_isolated_test_window = win
        ext._lam_rp_isolated_test_widget = vw
        ext._lam_rp_isolated_test_result = result
    except Exception:
        pass
    return result


def teardown_isolated_rp_test_window(ext: Any = None) -> None:
    """독립 Window RP 실험 창 정리 — embedded Widget 생성 전 aux context 중복 방지."""
    vw = None
    win = None
    if ext is not None:
        try:
            vw = getattr(ext, "_lam_rp_isolated_test_widget", None)
            win = getattr(ext, "_lam_rp_isolated_test_window", None)
        except Exception:
            vw = None
            win = None
    if vw is None and win is None:
        return
    try:
        print(f"{_ISOLATED_TEST_TAG} teardown isolated test window", flush=True)
    except Exception:
        pass
    try:
        for meth in ("destroy", "close"):
            fn = getattr(vw, meth, None)
            if callable(fn):
                fn()
                break
    except Exception:
        pass
    if win is not None:
        try:
            win.visible = False
        except Exception:
            pass
        try:
            fn = getattr(win, "destroy", None)
            if callable(fn):
                fn()
        except Exception:
            pass
    if ext is not None:
        try:
            ext._lam_rp_isolated_test_window = None
            ext._lam_rp_isolated_test_widget = None
        except Exception:
            pass


def log_finalize_rp_step(aux_rec: Any, step: str) -> None:
    """``finalize_widget_split_startup`` 각 단계 직후 RP 상태만 기록."""
    if not viewport_rp_diag_enabled():
        return
    if not isinstance(aux_rec, dict):
        return
    wn = str(aux_rec.get("_win_name") or "LAM_SimSplit_1")
    api = aux_rec.get("api")
    widget = aux_rec.get("widget")
    if api is None and widget is not None:
        try:
            api = getattr(widget, "viewport_api", None)
        except Exception:
            api = None
    probe = probe_render_product_chain(api, widget, scene_view=aux_rec.get("scene_view"))
    try:
        print(
            f"[LAM/rp-finalize] step={step!r} tile={wn!r} "
            f"HAS_RP={probe.get('HAS_RENDER_PRODUCT')} "
            f"path={probe.get('api.get_render_product_path()')} "
            f"hydra={probe.get('api.hydra_engine')} "
            f"viewport_id={probe.get('api.id')}",
            flush=True,
        )
    except Exception:
        pass


__all__ = [
    "viewport_rp_diag_enabled",
    "viewport_rp_isolated_test_enabled",
    "viewport_rp_timeline_frames",
    "probe_render_product_chain",
    "log_rp_investigation",
    "observe_rp_timeline",
    "run_isolated_viewport_widget_test",
    "teardown_isolated_rp_test_window",
    "log_finalize_rp_step",
]

"""
P0-B (ViewportWidget 2분할 톤/조명 불일치) 진단·실험 fix 모듈.

배경
----
Widget 분할에서 화면1(기본 ctx / master_1)과 화면2(aux ctx / master_2)의
RenderProduct·Hydra는 모두 정상인데, 배경/ambient/톤이 다르게 보이는 문제(P0-B).

역할
----
READY 직후 SSOT 후보( ViewportAPI / carb.settings / SessionLayer / UsdLux / Hydra )를
로그로 비교해, 톤 차이의 실제 계층을 좁힌다.
실제 LightRig 복제·fallback DomeLight 제거는 `sim_multi_view_widget.py` 쪽이 담당하고,
본 모듈은 주로 진단 + (옵션) session TransferContent 실험용이다.

로그 prefix
-----------
  [TBS/p0b-api]          ViewportAPI 속성 dump
  [TBS/p0b-api-diff]     main vs aux 의미 있는 API 차이만
  [TBS/p0b-carb]         carb.settings tree dump
  [TBS/p0b-session]      root/session layer identifier·rootPrims
  [TBS/p0b-light]        UsdLux 조명 목록·Dome intensity
  [TBS/p0b-light-timing] 프레임 +1/+2/+5 Lux 타이밍
  [TBS/p0b-rp]           RenderProduct / Hydra 요약
  [TBS/p0b-introspect]   Kit viewport 관련 모듈 callable 목록
  [TBS/p0b-active]       get_active_viewport() 변화 추적

환경 변수
---------
  TBS_P0B_DIAG=1           → 진단 ON (기본은 OFF — 조사 완료 후 콘솔 스팸 방지)
  TBS_P0B_FIX=1            → session TransferContent 실험 fix ON
  TBS_P0B_FRAME_TRACK=1    → 120프레임 톤 추적 (TBS_P0B_DIAG=1 일 때만)
  TBS_P0B_DISABLE_FALLBACK=1 → `_ensure_aux_stage_default_lighting` 전체 skip (실험 4)
  TBS_P0B_DISABLE_CLONE=1  → LightRig early sync + post-READY clone skip (실험 5)

로그 (ordered investigation)
----------------------------
  [TBS/p0b-ord]   함수 호출 순서 (frame/ts/lux/api/rp)
  [TBS/p0b-frame] 120프레임 lux·tone·carb dump
  [TBS/p0b-who]   TBS_DefaultDomeLight 생성 stack
  [TBS/p0b-rs]    RenderSettings / Environment dump
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from typing import Any, Dict, List, Optional, Sequence, Tuple

import omni.kit.app as kit_app


def _env_flag(name: str) -> bool:
    """환경 변수 name 이 1/true/yes/on 이면 True. 파싱 실패 시 False."""
    try:
        v = str(os.environ.get(name, "") or "").strip().lower()
    except Exception:
        return False
    return v in ("1", "true", "yes", "on")


def p0b_diag_enabled() -> bool:
    """
    P0-B 진단 로그 출력 여부.

    기본 OFF (톤 이슈 해결 후 콘솔 스팸 방지).
    재조사 시 `TBS_P0B_DIAG=1` 로 켠다.
    """
    return _env_flag("TBS_P0B_DIAG")


def p0b_log(msg: str) -> None:
    """Verbose P0-B log — only when TBS_P0B_DIAG=1."""
    if not p0b_diag_enabled():
        return
    try:
        print(msg, flush=True)
    except Exception:
        pass


def p0b_fix_enabled() -> bool:
    """
    실험용 session-layer clone fix 게이트.

    조사 중 의도치 않은 stage 변조를 막기 위해 `TBS_P0B_FIX=1` 일 때만 True.
    (실사용 LightRig 복제는 sim_multi_view_widget 의 clone 경로가 담당.)
    """
    return _env_flag("TBS_P0B_FIX")


def _fmt(val: Any, *, max_len: int = 240) -> str:
    """로그용 값 포맷. 단순 타입은 그대로, 객체는 type@id 형태로 짧게."""
    if val is None:
        return "None"
    if isinstance(val, (bool, int, float)):
        return repr(val)
    if isinstance(val, str):
        return val if len(val) <= max_len else val[: max_len - 3] + "..."
    try:
        return f"{type(val).__name__}@{id(val):#x}"
    except Exception:
        return "<?>"


def dump_viewport_api(api: Any, label: str) -> List[str]:
    """
    ViewportAPI(또는 Proxy)의 non-callable public 속성을 전부 덤프.

    화면1/2 톤 차이의 API 계층 SSOT 여부를 확인하기 위함.
    dict/큰 list 는 로그 폭주 방지를 위해 생략.
    prefix: [TBS/p0b-api]
    """
    lines: List[str] = []
    for name in sorted(set(dir(api)) if api is not None else ()):
        if name.startswith("_"):
            continue
        try:
            value = getattr(api, name)
        except Exception:
            continue
        if callable(value):
            continue
        # keep log size bounded; ignore huge containers
        if isinstance(value, (dict, set)):
            continue
        if isinstance(value, (list, tuple)) and len(value) > 32:
            continue
        lines.append(f"[TBS/p0b-api] {label} {name}={_fmt(value)}")
    return lines


_P0B_API_DIFF_KEYS: Tuple[str, ...] = (
    # 톤/배경/그리드 고신호 필드 (빌드별 이름 변형 포함)
    "background_color",
    "background_enabled",
    "background_enable",
    "ambient_light_color",
    "ambient_light_intensity",
    "exposure",
    "auto_exposure",
    "tone_mapping",
    "hdr",
    "ibl_enabled",
    "environment_texture",
    "show_grid",
    "grid_scale",
    "renderer_settings",
    "color_space",
    "environment_map",
    "environment_map_path",
    "exposure_compensation",
)


def _api_get(api: Any, key: str) -> Any:
    """ViewportAPI 에서 key 속성을 안전하게 읽는다. 없으면 None."""
    if api is None:
        return None
    try:
        if hasattr(api, key):
            return getattr(api, key)
    except Exception:
        return None
    return None


def dump_viewport_api_diff(main_api: Any, aux_api: Any) -> List[str]:
    """
    화면1 vs 화면2 ViewportAPI 차이만 로그.

    1) `_P0B_API_DIFF_KEYS` (톤/배경 관련) 우선 비교
    2) 그 외 공통 단순 타입 속성 중 다른 것만 (최대 80개)

    → API 계층만으로 톤이 갈리는지 빠르게 판정.
    prefix: [TBS/p0b-api-diff]
    """
    lines: List[str] = []
    if main_api is None or aux_api is None:
        return ["[TBS/p0b-api-diff] api=None"]

    # 1) Key list (explicit)
    for k in _P0B_API_DIFF_KEYS:
        mv = _api_get(main_api, k)
        av = _api_get(aux_api, k)
        try:
            same = mv == av
        except Exception:
            same = False
        if not same:
            lines.append(f"[TBS/p0b-api-diff] {k} main={_fmt(mv)} aux={_fmt(av)}")

    # 2) Light generic scan: simple-typed attributes that differ (bounded)
    try:
        mset = {x for x in dir(main_api) if not x.startswith("_")}
        aset = {x for x in dir(aux_api) if not x.startswith("_")}
        common = sorted(mset.intersection(aset))
    except Exception:
        common = []

    extra_count = 0
    for k in common:
        if k in _P0B_API_DIFF_KEYS:
            continue
        try:
            mv = getattr(main_api, k)
            av = getattr(aux_api, k)
        except Exception:
            continue
        if callable(mv) or callable(av):
            continue
        if isinstance(mv, (dict, set)) or isinstance(av, (dict, set)):
            continue
        if isinstance(mv, (list, tuple)) and len(mv) > 32:
            continue
        if isinstance(av, (list, tuple)) and len(av) > 32:
            continue
        # Only include simple types to keep output bounded.
        if not isinstance(mv, (bool, int, float, str, tuple, list)) or not isinstance(
            av, (bool, int, float, str, tuple, list)
        ):
            continue
        try:
            same = mv == av
        except Exception:
            same = False
        if not same:
            lines.append(f"[TBS/p0b-api-diff] {k} main={_fmt(mv)} aux={_fmt(av)}")
            extra_count += 1
            if extra_count >= 80:
                lines.append("[TBS/p0b-api-diff] ... truncated (too many diffs)")
                break

    return lines


def _settings_child_keys(settings: Any, path: str) -> List[str]:
    """
    carb.settings path 의 자식 키 목록.

    Kit 빌드마다 API 이름이 달라 get_child_keys / get_children 등을 순차 시도.
    """
    for meth in ("get_child_keys", "get_children", "get_child_keys_as_list"):
        fn = getattr(settings, meth, None)
        if callable(fn):
            try:
                out = fn(path)
                if isinstance(out, (list, tuple)):
                    return [str(x) for x in out]
            except Exception:
                pass
    return []


def dump_carb_settings(prefixes: Sequence[str], *, label: str) -> List[str]:
    """
    carb.settings 트리 dump (BFS).

    뷰포트/렌더 전역 설정(`/rtx`, `/renderer`, `/exts/omni.kit.viewport` …)이
    tile별로 분리되는지·톤 SSOT인지 확인.
    prefix: [TBS/p0b-carb]
    """
    lines: List[str] = []
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
    except Exception as exc:
        return [f"[TBS/p0b-carb] {label} err={exc}"]

    visited: set[str] = set()
    stack: List[str] = [str(p) for p in prefixes]
    while stack:
        path = stack.pop()
        if not path or path in visited:
            continue
        visited.add(path)
        try:
            val = settings.get(path)
        except Exception:
            val = None
        lines.append(f"[TBS/p0b-carb] {label} {path}={_fmt(val)}")
        for child in _settings_child_keys(settings, path):
            c = str(child).strip("/")
            if not c:
                continue
            child_path = path.rstrip("/") + "/" + c
            if child_path not in visited:
                stack.append(child_path)
    return lines


def _named_usd_context(ctx_name: str) -> Any:
    """usd_context_name 으로 omni.usd context 핸들 획득. 실패 시 None."""
    try:
        import omni.usd  # type: ignore

        return omni.usd.get_context(str(ctx_name or ""))
    except Exception:
        return None


def _stage_for_ctx(ctx_name: str) -> Any:
    """해당 context 의 USD stage. 없으면 None."""
    ctx = _named_usd_context(ctx_name)
    if ctx is None:
        return None
    try:
        return ctx.get_stage() if hasattr(ctx, "get_stage") else None
    except Exception:
        return None


def dump_session_layers(*, label: str, ctx_name: str) -> List[str]:
    """
    root/session layer identifier, sublayers, top-level rootPrims 요약.

    LightRig(`/OmniKit_Viewport_LightRig`)가 어느 레이어 rootPrims 에 보이는지
    (session vs root vs 둘 다 이름만) 확인하는 핵심 덤프.
    prefix: [TBS/p0b-session]
    """
    lines: List[str] = []
    try:
        from pxr import Sdf  # noqa: F401
    except Exception as exc:
        return [f"[TBS/p0b-session] {label} ctx={ctx_name!r} pxr_err={exc}"]

    st = _stage_for_ctx(ctx_name)
    if st is None:
        return [f"[TBS/p0b-session] {label} ctx={ctx_name!r} stage=None"]
    try:
        root = st.GetRootLayer()
        sess = st.GetSessionLayer()
        lines.append(
            f"[TBS/p0b-session] {label} ctx={ctx_name!r} root={getattr(root, 'identifier', None)!r} "
            f"session={getattr(sess, 'identifier', None)!r}"
        )
        for lyr, nm in ((root, "root"), (sess, "session")):
            if lyr is None:
                continue
            try:
                sub = list(getattr(lyr, "subLayerPaths", []) or [])
            except Exception:
                sub = []
            lines.append(f"[TBS/p0b-session] {label} {nm}.sublayers={sub!r}")
            # list top-level specs (paths only; avoid huge dumps)
            try:
                prim_paths = list(getattr(lyr, "rootPrims", []) or [])
                pp = [str(getattr(p, "path", p)) for p in prim_paths]
            except Exception:
                pp = []
            if pp:
                lines.append(f"[TBS/p0b-session] {label} {nm}.rootPrims={pp!r}")
    except Exception as exc:
        lines.append(f"[TBS/p0b-session] {label} ctx={ctx_name!r} err={exc}")
    return lines


def dump_usdlux(*, label: str, ctx_name: str) -> Tuple[List[str], List[str]]:
    """
    Stage 를 Traverse 해 UsdLux / typed light prim 을 수집.

    Returns
    -------
    (log_lines, light_paths)

    Kit 기본 Viewport LightRig 는 `UsdLux.Light` 만으로는 놓칠 수 있어
    LightAPI + DomeLight/DistantLight 등 typeName 도 함께 본다.
    Dome 의 texture/intensity 도 함께 남겨 화면1(보통 1.0) vs
    fallback(보통 300.0) 차이를 smoking-gun 으로 쓴다.
    prefix: [TBS/p0b-light]
    """
    lines: List[str] = []
    st = _stage_for_ctx(ctx_name)
    if st is None:
        return ([f"[TBS/p0b-light] {label} ctx={ctx_name!r} stage=None"], [])
    try:
        from pxr import UsdLux
    except Exception as exc:
        return ([f"[TBS/p0b-light] {label} ctx={ctx_name!r} pxr_err={exc}"], [])
    paths: List[str] = []
    typed: List[Tuple[str, str]] = []
    try:
        light_types = frozenset(
            {
                "DomeLight",
                "DistantLight",
                "RectLight",
                "DiskLight",
                "SphereLight",
                "CylinderLight",
                "PortalLight",
            }
        )
        for prim in st.Traverse():
            try:
                tname = str(prim.GetTypeName() or "")
                is_light = False
                try:
                    if prim.IsA(UsdLux.Light):
                        is_light = True
                except Exception:
                    pass
                try:
                    if prim.IsA(UsdLux.LightAPI):
                        is_light = True
                except Exception:
                    pass
                if tname in light_types:
                    is_light = True
                if is_light:
                    p = str(prim.GetPath())
                    paths.append(p)
                    typed.append((p, tname))
            except Exception:
                continue
        lines.append(f"[TBS/p0b-light] {label} ctx={ctx_name!r} light_count={len(paths)}")
        if paths:
            lines.append(f"[TBS/p0b-light] {label} ctx={ctx_name!r} lights={paths!r}")
            try:
                lines.append(f"[TBS/p0b-light] {label} ctx={ctx_name!r} light_types={typed!r}")
            except Exception:
                pass
        for p in paths:
            prim = st.GetPrimAtPath(p)
            if not prim or not prim.IsValid():
                continue
            try:
                dome = UsdLux.DomeLight(prim)
                if dome:
                    tex = dome.GetTextureFileAttr().Get() if dome.GetTextureFileAttr() else None
                    inten = dome.GetIntensityAttr().Get() if dome.GetIntensityAttr() else None
                    lines.append(
                        f"[TBS/p0b-light] {label} dome={p} texture={tex!r} intensity={inten!r}"
                    )
            except Exception:
                pass
    except Exception as exc:
        lines.append(f"[TBS/p0b-light] {label} ctx={ctx_name!r} err={exc}")
    return (lines, paths)


def dump_rp_hydra(api: Any, *, label: str) -> List[str]:
    """
    RenderProduct / Hydra 체인 요약.

    톤 불일치가 “RP 미생성”인지 “엔진/mode만 같고 Lux가 다른지” 분리.
    (P0-B 에서는 보통 hydra/mode 동일 + Lux만 다름으로 결론.)
    prefix: [TBS/p0b-rp]
    """
    lines: List[str] = []
    if api is None:
        return [f"[TBS/p0b-rp] {label} api=None"]
    attrs = (
        "render_mode",
        "hydra_engine",
        "resolution",
        "full_resolution",
        "fps",
        "camera_path",
        "render_product_path",
        "usd_context_name",
    )
    for a in attrs:
        try:
            lines.append(f"[TBS/p0b-rp] {label} api.{a}={_fmt(getattr(api, a, None))}")
        except Exception:
            pass
    for m in ("get_render_product_path", "get_render_product", "get_render_settings", "get_renderer"):
        fn = getattr(api, m, None)
        if callable(fn):
            try:
                lines.append(f"[TBS/p0b-rp] {label} api.{m}()={_fmt(fn())}")
            except Exception as exc:
                lines.append(f"[TBS/p0b-rp] {label} api.{m}()=err:{exc}")
    return lines


def dump_viewport_modules_introspection() -> List[str]:
    """
    Kit viewport 관련 모듈의 callable 목록을 런타임 introspect.

    목적: embedded ViewportWidget 에 LightRig/environment 를
    공식 API로 주입·active 전환할 수 있는 함수가 있는지 탐색.
    (`omni.kit.viewport.lights` 등은 빌드에 따라 import 불가일 수 있음.)
    prefix: [TBS/p0b-introspect]
    """
    lines: List[str] = []

    def _list_callables(mod: Any, *, label: str) -> None:
        """단일 모듈 dir() 중 callable 만 모아 active/light/rig 키워드 우선 표시."""
        try:
            names: List[str] = []
            for n in dir(mod):
                if n.startswith("_"):
                    continue
                try:
                    v = getattr(mod, n)
                except Exception:
                    continue
                if callable(v):
                    names.append(str(n))
            hi = [
                n
                for n in names
                if any(
                    k in n.lower()
                    for k in (
                        "active",
                        "focus",
                        "select",
                        "set",
                        "light",
                        "rig",
                        "env",
                        "exposure",
                        "tone",
                        "profile",
                        "viewport",
                    )
                )
            ]
            lo = [n for n in names if n not in hi]
            show = (sorted(hi) + sorted(lo))[:140]
            lines.append(
                f"[TBS/p0b-introspect] {label} callables_count={len(names)} show={show!r}"
            )
        except Exception as exc:
            lines.append(f"[TBS/p0b-introspect] {label} err={exc}")

    try:
        import omni.kit.viewport.utility as vputil  # type: ignore

        _list_callables(vputil, label="omni.kit.viewport.utility")
    except Exception as exc:
        lines.append(
            f"[TBS/p0b-introspect] omni.kit.viewport.utility import_err={exc}"
        )

    for mod_name in (
        "omni.kit.viewport.lights",
        "omni.kit.window.viewport",
        "omni.kit.viewport.window",
    ):
        try:
            mod = __import__(mod_name, fromlist=["*"])
            _list_callables(mod, label=mod_name)
        except Exception as exc:
            lines.append(f"[TBS/p0b-introspect] {mod_name} import_err={exc}")

    return lines


def _copy_session_layer_content(src_stage: Any, dst_stage: Any) -> bool:
    """
    main session → aux session 전체 content 복제 (실험용).

    `TransferContent` 우선, 실패 시 rootPrims 에 대해 `Sdf.CopySpec`.
    TBS_P0B_FIX=1 일 때만 run_p0b_fix_if_needed 경유로 호출된다.
    """
    if src_stage is None or dst_stage is None:
        return False
    try:
        src = src_stage.GetSessionLayer()
        dst = dst_stage.GetSessionLayer()
        if src is None or dst is None:
            return False
        try:
            dst.TransferContent(src)  # type: ignore[attr-defined]
            return True
        except Exception:
            pass
        try:
            from pxr import Sdf
        except Exception:
            return False
        copied = 0
        for prim in getattr(src, "rootPrims", []) or []:
            p = getattr(prim, "path", None)
            if p is None:
                continue
            try:
                Sdf.CopySpec(src, p, dst, p)
                copied += 1
            except Exception:
                pass
        return copied > 0
    except Exception:
        return False


def run_p0b_diagnostics(
    *,
    main_api: Any,
    aux_api: Any,
    main_ctx: str = "",
    aux_ctx: str = "morph_tbs_split_aux_1",
) -> None:
    """
    P0-B 진단 오케스트레이터 (동기).

    finalize_widget_split_startup() READY 후 호출되어
    introspect → API dump/diff → carb → session → UsdLux → RP/Hydra
    순으로 콘솔에 찍는다. p0b_diag_enabled()=False 면 no-op.
    """
    if not p0b_diag_enabled():
        return

    for ln in dump_viewport_modules_introspection():
        print(ln, flush=True)
    for ln in dump_viewport_api(main_api, "main"):
        print(ln, flush=True)
    for ln in dump_viewport_api(aux_api, "aux"):
        print(ln, flush=True)
    for ln in dump_viewport_api_diff(main_api, aux_api):
        print(ln, flush=True)

    prefixes = ("/rtx", "/renderer", "/exts/omni.kit.viewport", "/exts/omni.kit.window.viewport")
    for ln in dump_carb_settings(prefixes, label="runtime"):
        print(ln, flush=True)

    for ln in dump_session_layers(label="main", ctx_name=main_ctx):
        print(ln, flush=True)
    for ln in dump_session_layers(label="aux", ctx_name=aux_ctx):
        print(ln, flush=True)
    main_lux_lines, _main_lux_paths = dump_usdlux(label="main", ctx_name=main_ctx)
    aux_lux_lines, _aux_lux_paths = dump_usdlux(label="aux", ctx_name=aux_ctx)
    for ln in main_lux_lines + aux_lux_lines:
        print(ln, flush=True)

    for ln in dump_rp_hydra(main_api, label="main"):
        print(ln, flush=True)
    for ln in dump_rp_hydra(aux_api, label="aux"):
        print(ln, flush=True)


def run_p0b_fix_if_needed(
    *,
    main_ctx: str = "",
    aux_ctx: str = "morph_tbs_split_aux_1",
) -> None:
    """
    TBS_P0B_FIX=1 일 때만 실험용 루트-원인 fix 시도.

    조건: main 에 Lux 있고 aux 에 없거나, session identifier 가 다를 때
    `_copy_session_layer_content` 로 session 전체를 aux 에 복제.

    주의: 실사용 LightRig 단일 subtree 복제·fallback 제거는
    `sim_multi_view_widget._clone_default_light_rig_from_main_to_aux` 가 담당.
    본 함수는 진단 단계의 옵션 실험이다.
    """
    if not p0b_fix_enabled():
        return
    main_stage = _stage_for_ctx(main_ctx)
    aux_stage = _stage_for_ctx(aux_ctx)
    if main_stage is None or aux_stage is None:
        return
    # If aux has no UsdLux lights but main has, session/lux mismatch is likely.
    _, main_paths = dump_usdlux(label="probe-main", ctx_name=main_ctx)
    _, aux_paths = dump_usdlux(label="probe-aux", ctx_name=aux_ctx)
    need = bool(main_paths) and not bool(aux_paths)
    # Also treat different session identifiers as a signal.
    try:
        need = need or (
            main_stage.GetSessionLayer().identifier != aux_stage.GetSessionLayer().identifier
        )
    except Exception:
        pass
    if not need:
        return
    ok = _copy_session_layer_content(main_stage, aux_stage)
    try:
        print(
            f"[TBS/p0b-session] fix_applied={bool(ok)} main_ctx={main_ctx!r} aux_ctx={aux_ctx!r}",
            flush=True,
        )
    except Exception:
        pass


def _active_viewport_snapshot() -> Dict[str, str]:
    """
    `get_active_viewport()` 스냅샷 (id / usd_context_name).

    native Workspace Viewport(#0) 가 계속 active 인지,
    embedded 타일이 active 가 될 수 있는지 추적에 사용.
    """
    out: Dict[str, str] = {}
    try:
        from omni.kit.viewport.utility import get_active_viewport

        vp = get_active_viewport()
        out["active"] = _fmt(vp)
        if vp is not None:
            try:
                out["id"] = str(getattr(vp, "id", None) or "")
            except Exception:
                pass
            try:
                out["usd_context_name"] = str(
                    getattr(vp, "usd_context_name", None)
                    or getattr(vp, "context_name", None)
                    or ""
                )
            except Exception:
                pass
    except Exception as exc:
        out["err"] = str(exc)
    return out


async def run_p0b_light_timing_and_active_tracker(
    *,
    main_ctx: str,
    aux_ctx: str,
    frames: Sequence[int] = (1, 2, 5),
    carb_prefixes: Sequence[str] = (
        "/rtx",
        "/renderer",
        "/exts/omni.kit.viewport",
        "/exts/omni.kit.window.viewport",
    ),
) -> None:
    """
    aux ViewportWidget 연결 직후 N프레임 동안 비동기 추적.

    - frame+1/+2/+5: main/aux UsdLux 변화 (Kit 가 LightRig 를 늦게 심는지)
    - active viewport 가 바뀔 때마다 id/ctx + carb dump

    LightRig 생성 타이밍 / active·native 결합 가설 검증용.
    prefix: [TBS/p0b-active], [TBS/p0b-light-timing]
    """
    if not p0b_diag_enabled():
        return
    try:
        targets = sorted({max(0, int(x)) for x in frames})
    except Exception:
        targets = [1, 2, 5]

    try:
        print(
            f"[TBS/p0b-active] tracker_start main_ctx={main_ctx!r} aux_ctx={aux_ctx!r} frames={list(frames)!r}",
            flush=True,
        )
    except Exception:
        pass

    last_active: Optional[str] = None
    f = 0
    max_f = max(targets) if targets else 0
    while f <= max_f:
        # snapshot active every frame but only log on change
        snap = _active_viewport_snapshot()
        cur = f"{snap.get('id','')}|{snap.get('usd_context_name','')}|{snap.get('active','')}"
        if cur != last_active:
            last_active = cur
            try:
                print(
                    f"[TBS/p0b-active] frame={f} id={snap.get('id')!r} "
                    f"ctx={snap.get('usd_context_name')!r} active={snap.get('active')}",
                    flush=True,
                )
            except Exception:
                pass
            for ln in dump_carb_settings(
                carb_prefixes,
                label=f"active@{f}:{snap.get('id','')}:{snap.get('usd_context_name','')}",
            ):
                print(ln, flush=True)

        if f in targets:
            try:
                main_lines, _ = dump_usdlux(label=f"timing+{f}:main", ctx_name=main_ctx)
                aux_lines, _ = dump_usdlux(label=f"timing+{f}:aux", ctx_name=aux_ctx)
                for ln in main_lines + aux_lines:
                    print(
                        f"[TBS/p0b-light-timing] "
                        f"{ln[len('[TBS/p0b-light] '):] if ln.startswith('[TBS/p0b-light] ') else ln}",
                        flush=True,
                    )
            except Exception as exc:
                try:
                    print(f"[TBS/p0b-light-timing] frame={f} err={exc}", flush=True)
                except Exception:
                    pass

        f += 1
        try:
            await kit_app.get_app().next_update_async()
        except Exception:
            break


# =============================================================================
# P0-B Ordered Investigation (no speculative patches)
# Goal: answer Q1–Q5 with logs only. CopySpec / intensity tuning forbidden here.
# Env:
#   TBS_P0B_DISABLE_FALLBACK=1  → skip /World/TBS_DefaultDomeLight creation
#   TBS_P0B_DISABLE_CLONE=1     → skip LightRig CopySpec clone
#   TBS_P0B_FRAME_TRACK=0       → disable 120-frame tracker (default ON with diag)
# Logs: [TBS/p0b-ord] [TBS/p0b-frame] [TBS/p0b-who] [TBS/p0b-rs]
# =============================================================================


def p0b_disable_fallback() -> bool:
    """True if fallback DomeLight auto-create must be skipped (experiment 4)."""
    return _env_flag("TBS_P0B_DISABLE_FALLBACK")


def p0b_disable_clone() -> bool:
    """True if LightRig CloneSpec path must be skipped (experiment 5)."""
    return _env_flag("TBS_P0B_DISABLE_CLONE")


def p0b_frame_track_enabled() -> bool:
    """120-frame tone tracker; explicit opt-in via TBS_P0B_FRAME_TRACK=1 with diag ON."""
    if not p0b_diag_enabled():
        return False
    return _env_flag("TBS_P0B_FRAME_TRACK")


def _kit_frame_number() -> int:
    try:
        return int(kit_app.get_app().get_update_number())
    except Exception:
        return -1


def _lux_paths_compact(ctx_name: str) -> List[str]:
    _, paths = dump_usdlux(label="ord", ctx_name=ctx_name)
    return list(paths or [])


def _api_snap(api: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if api is None:
        return out
    for k in (
        "id",
        "usd_context_name",
        "render_product_path",
        "render_mode",
        "hydra_engine",
        "background_color",
        "ambient_light_intensity",
        "show_grid",
        "hdr",
        "exposure",
        "fill_frame",
    ):
        try:
            out[k] = getattr(api, k, None)
        except Exception:
            out[k] = None
    try:
        fn = getattr(api, "get_render_product_path", None)
        if callable(fn):
            out["get_render_product_path"] = fn()
    except Exception:
        pass
    return out


def log_p0b_call_order(
    step: str,
    *,
    main_ctx: str = "",
    aux_ctx: str = "morph_tbs_split_aux_1",
    main_api: Any = None,
    aux_api: Any = None,
    extra: str = "",
) -> None:
    """
    Call-order breadcrumb for lighting/finalize path.

    Logs frame, wall time, main/aux lux, ViewportAPI id, RenderProduct path.
    prefix: [TBS/p0b-ord]
    """
    if not p0b_diag_enabled():
        return
    try:
        frame = _kit_frame_number()
        ts = time.time()
        main_lux = _lux_paths_compact(main_ctx)
        aux_lux = _lux_paths_compact(aux_ctx)
        ms = _api_snap(main_api)
        as_ = _api_snap(aux_api)
        print(
            f"[TBS/p0b-ord] step={step!r} frame={frame} ts={ts:.3f} "
            f"main_lux={main_lux!r} aux_lux={aux_lux!r} "
            f"main_api_id={ms.get('id')!r} aux_api_id={as_.get('id')!r} "
            f"main_rp={ms.get('render_product_path') or ms.get('get_render_product_path')!r} "
            f"aux_rp={as_.get('render_product_path') or as_.get('get_render_product_path')!r} "
            f"{extra}",
            flush=True,
        )
    except Exception as exc:
        try:
            print(f"[TBS/p0b-ord] step={step!r} err={exc}", flush=True)
        except Exception:
            pass


def log_fallback_create_stack(*, ctx_name: str, light_path: str) -> None:
    """
    WHO created /World/TBS_DefaultDomeLight — dump stack at Define site.
    prefix: [TBS/p0b-who]
    """
    if not p0b_diag_enabled():
        return
    try:
        stack = "".join(traceback.format_stack(limit=24))
        print(
            f"[TBS/p0b-who] CREATE {light_path!r} ctx={ctx_name!r} "
            f"frame={_kit_frame_number()} ts={time.time():.3f}\n{stack}",
            flush=True,
        )
    except Exception as exc:
        try:
            print(f"[TBS/p0b-who] CREATE err={exc}", flush=True)
        except Exception:
            pass


def dump_render_settings(*, label: str, ctx_name: str) -> List[str]:
    """
    Dump RenderSettings / environment-ish prims on stage.
    prefix: [TBS/p0b-rs]
    """
    lines: List[str] = []
    st = _stage_for_ctx(ctx_name)
    if st is None:
        return [f"[TBS/p0b-rs] {label} ctx={ctx_name!r} stage=None"]
    try:
        interesting: List[str] = []
        for prim in st.Traverse():
            try:
                tname = str(prim.GetTypeName() or "")
                path = str(prim.GetPath())
                if tname in ("RenderSettings", "RenderProduct", "RenderVar") or (
                    "Environment" in path or "RenderSettings" in path or path.startswith("/Render")
                ):
                    interesting.append(f"{path}({tname})")
                    # attribute sample (bounded)
                    attrs_out: List[str] = []
                    for attr in prim.GetAttributes():
                        try:
                            name = attr.GetName()
                            if not any(
                                k in name.lower()
                                for k in (
                                    "expos",
                                    "tone",
                                    "env",
                                    "dome",
                                    "ibl",
                                    "background",
                                    "ambient",
                                    "intensity",
                                    "file",
                                    "enable",
                                )
                            ):
                                continue
                            attrs_out.append(f"{name}={attr.Get()!r}")
                            if len(attrs_out) >= 12:
                                break
                        except Exception:
                            continue
                    if attrs_out:
                        lines.append(
                            f"[TBS/p0b-rs] {label} {path} attrs={attrs_out!r}"
                        )
            except Exception:
                continue
        lines.insert(
            0,
            f"[TBS/p0b-rs] {label} ctx={ctx_name!r} prims={interesting[:40]!r}",
        )
    except Exception as exc:
        lines.append(f"[TBS/p0b-rs] {label} err={exc}")
    return lines


def _carb_tone_keys() -> List[str]:
    """High-signal carb paths often related to viewport tone / lighting mode."""
    return [
        "/rtx/post/tonemap/op",
        "/rtx/post/tonemap/filmIso",
        "/rtx/post/histogram/enabled",
        "/rtx/indirectDiffuse/enabled",
        "/exts/omni.kit.viewport.menubar.lighting",
        "/exts/omni.kit.viewport.window/lightingMode",
        "/persistent/app/viewport/displayOptions",
        "/app/viewport/grid/enabled",
        "/rtx/ambientOcclusion/enabled",
    ]


def dump_tone_carb_snapshot(*, label: str) -> List[str]:
    lines: List[str] = []
    try:
        import carb.settings  # type: ignore

        s = carb.settings.get_settings()
    except Exception as exc:
        return [f"[TBS/p0b-frame] {label} carb_err={exc}"]
    for path in _carb_tone_keys():
        try:
            lines.append(f"[TBS/p0b-frame] {label} carb {path}={_fmt(s.get(path))}")
        except Exception:
            lines.append(f"[TBS/p0b-frame] {label} carb {path}=<?>")
    return lines


def _tone_api_fields(api: Any) -> Dict[str, Any]:
    return _api_snap(api)


async def run_p0b_frame_tone_tracker(
    *,
    main_api: Any,
    aux_api: Any,
    main_ctx: str = "",
    aux_ctx: str = "morph_tbs_split_aux_1",
    frames: int = 120,
) -> None:
    """
    0..frames-1: dump lux + ViewportAPI tone fields + carb tone keys each frame.
    Detect first frame where main_lux != aux_lux (or intensity-like mismatch).
    Also dump RenderSettings once at start and on first mismatch.
    prefix: [TBS/p0b-frame]
    """
    if not p0b_frame_track_enabled():
        return
    try:
        n = max(1, int(frames))
    except Exception:
        n = 120

    print(
        f"[TBS/p0b-frame] tracker_start frames={n} main_ctx={main_ctx!r} aux_ctx={aux_ctx!r} "
        f"kit_frame={_kit_frame_number()} ts={time.time():.3f}",
        flush=True,
    )
    for ln in dump_render_settings(label="start:main", ctx_name=main_ctx):
        print(ln, flush=True)
    for ln in dump_render_settings(label="start:aux", ctx_name=aux_ctx):
        print(ln, flush=True)

    first_diff_frame: Optional[int] = None
    last_sig: Optional[str] = None

    for f in range(n):
        main_lux = _lux_paths_compact(main_ctx)
        aux_lux = _lux_paths_compact(aux_ctx)
        ms = _tone_api_fields(main_api)
        as_ = _tone_api_fields(aux_api)
        sig = f"ml={main_lux}|al={aux_lux}|mb={ms.get('background_color')}|ab={as_.get('background_color')}|ma={ms.get('ambient_light_intensity')}|aa={as_.get('ambient_light_intensity')}"
        changed = sig != last_sig
        last_sig = sig
        same_lux = main_lux == aux_lux
        # Log every frame but compact; expand when lux/API tone fields change.
        line = (
            f"[TBS/p0b-frame] f={f}/{n-1} kit={_kit_frame_number()} "
            f"same_lux={same_lux} changed={changed} "
            f"main_lux={main_lux!r} aux_lux={aux_lux!r} "
            f"main_bg={ms.get('background_color')!r} aux_bg={as_.get('background_color')!r} "
            f"main_amb={ms.get('ambient_light_intensity')!r} aux_amb={as_.get('ambient_light_intensity')!r} "
            f"main_grid={ms.get('show_grid')!r} aux_grid={as_.get('show_grid')!r} "
            f"main_hdr={ms.get('hdr')!r} aux_hdr={as_.get('hdr')!r} "
            f"main_mode={ms.get('render_mode')!r} aux_mode={as_.get('render_mode')!r}"
        )
        print(line, flush=True)
        if changed or f in (0, 1, 2, 5, 10, 30, 60, 90, n - 1):
            for ln in dump_tone_carb_snapshot(label=f"f={f}"):
                print(ln, flush=True)

        if first_diff_frame is None and not same_lux:
            first_diff_frame = f
            print(
                f"[TBS/p0b-frame] FIRST_LUX_DIFF f={f} kit={_kit_frame_number()} "
                f"main_lux={main_lux!r} aux_lux={aux_lux!r}",
                flush=True,
            )
            for ln in dump_render_settings(label=f"diff@{f}:main", ctx_name=main_ctx):
                print(ln, flush=True)
            for ln in dump_render_settings(label=f"diff@{f}:aux", ctx_name=aux_ctx):
                print(ln, flush=True)
            for ln in dump_viewport_api_diff(main_api, aux_api):
                print(ln, flush=True)

        try:
            await kit_app.get_app().next_update_async()
        except Exception:
            break

    print(
        f"[TBS/p0b-frame] tracker_end first_lux_diff_frame={first_diff_frame!r} "
        f"kit={_kit_frame_number()} ts={time.time():.3f}",
        flush=True,
    )


_FALLBACK_WHO_KEYS = (
    "/World/TBS_DefaultDomeLight",
    "TBS_DefaultDomeLight",
)
_fallback_who_subs: Dict[str, Any] = {}
_frame_tracker_once = False


def reset_p0b_investigation_once_flags() -> None:
    """Allow re-arming trackers (e.g. extension reload)."""
    global _frame_tracker_once
    _frame_tracker_once = False


def install_fallback_who_notice(*, ctx_name: str, light_path: str = "/World/TBS_DefaultDomeLight") -> None:
    """
    Usd.Notice.ObjectsChanged on aux stage — log stack when fallback DomeLight appears
    even if our Define site was not the author (Kit / other code).
    prefix: [TBS/p0b-who]
    """
    if not p0b_diag_enabled():
        return
    ctx_name = str(ctx_name or "").strip()
    if not ctx_name or ctx_name in _fallback_who_subs:
        return
    st = _stage_for_ctx(ctx_name)
    if st is None:
        return
    try:
        from pxr import Tf, Usd  # type: ignore
    except Exception as exc:
        try:
            print(f"[TBS/p0b-who] notice_install_err={exc}", flush=True)
        except Exception:
            pass
        return

    target = str(light_path)

    def _on_objects_changed(notice, sender) -> None:  # noqa: ANN001
        try:
            paths = []
            for fn_name in ("GetResyncedPaths", "GetChangedInfoOnlyPaths"):
                fn = getattr(notice, fn_name, None)
                if not callable(fn):
                    continue
                try:
                    for p in fn() or []:
                        paths.append(str(p))
                except Exception:
                    pass
            hit = any(target in p or p.endswith("TBS_DefaultDomeLight") for p in paths)
            if not hit:
                return
            stack = "".join(traceback.format_stack(limit=20))
            print(
                f"[TBS/p0b-who] NOTICE path_hit={target!r} ctx={ctx_name!r} "
                f"frame={_kit_frame_number()} ts={time.time():.3f} "
                f"changed={paths[:24]!r}\n{stack}",
                flush=True,
            )
        except Exception as exc:
            try:
                print(f"[TBS/p0b-who] NOTICE err={exc}", flush=True)
            except Exception:
                pass

    try:
        key = Tf.Notice.Register(Usd.Notice.ObjectsChanged, _on_objects_changed, st)
        _fallback_who_subs[ctx_name] = key
        print(
            f"[TBS/p0b-who] notice_installed ctx={ctx_name!r} path={target!r} "
            f"frame={_kit_frame_number()}",
            flush=True,
        )
    except Exception as exc:
        try:
            print(f"[TBS/p0b-who] notice_install_fail={exc}", flush=True)
        except Exception:
            pass


def schedule_p0b_frame_tone_tracker_once(
    *,
    main_api: Any = None,
    aux_api: Any = None,
    main_ctx: str = "",
    aux_ctx: str = "morph_tbs_split_aux_1",
    frames: int = 120,
    label: str = "default",
) -> bool:
    """
    Start 120-frame tracker at most once per process/reload.
    Returns True if scheduled.
    """
    global _frame_tracker_once
    if not p0b_frame_track_enabled():
        return False
    if _frame_tracker_once:
        try:
            print(f"[TBS/p0b-frame] skip_already_started label={label!r}", flush=True)
        except Exception:
            pass
        return False
    _frame_tracker_once = True
    try:
        print(
            f"[TBS/p0b-frame] schedule_once label={label!r} frames={frames} "
            f"kit={_kit_frame_number()}",
            flush=True,
        )
        # Kit IApp has no create_task — use asyncio (same pattern as sim_multi_view).
        asyncio.ensure_future(
            run_p0b_frame_tone_tracker(
                main_api=main_api,
                aux_api=aux_api,
                main_ctx=main_ctx,
                aux_ctx=aux_ctx,
                frames=frames,
            )
        )
        return True
    except Exception as exc:
        _frame_tracker_once = False
        try:
            print(f"[TBS/p0b-frame] schedule_fail label={label!r} err={exc}", flush=True)
        except Exception:
            pass
        return False

"""분할 화면 보조 타일 — 합성 Master USD(Discover + Extract) 로드."""

from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tbs_composition_discovery import CompositionDiscovery
from .tbs_data_paths import resolve_local_data_path
from .tbs_instance_registry import AnimationInstanceRegistry
from .tbs_master_stage import MasterStage
from .tbs_playback_scheduler import PlaybackScheduler
from .tbs_runtime_evaluator import RuntimeEvaluator
from .tbs_types import AnimationInstance

_PRINT_PREFIX = "[TBS/split-load]"


@dataclass
class SplitScreenRuntime:
    """화면 1개분 합성 USD 런타임(registry + evaluator + scheduler + master)."""

    screen: int
    context_name: Optional[str]
    master: MasterStage
    registry: AnimationInstanceRegistry
    evaluator: RuntimeEvaluator
    scheduler: PlaybackScheduler


def resolve_split_aux_usd_path(ext: Any, tile_index: int = 0) -> Optional[str]:
    """보조 분할 타일용 사전 준비 Master USD 경로. 없으면 None(기존 복제 방식)."""
    _ = ext  # 향후 타일별 경로 확장용
    _ = tile_index
    try:
        from . import tbs_usd_window as _tuw

        raw = str(getattr(_tuw, "default_aux_load_usd_path", "") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        return None
    resolved = resolve_local_data_path(raw)
    if not resolved:
        try:
            print(
                f"{_PRINT_PREFIX} default_aux_load_usd_path 파일 없음: {raw!r} "
                f"(data/ 기준 또는 절대 경로 확인)",
                flush=True,
            )
        except Exception:
            pass
        return None
    return str(resolved).strip() or None


def aux_usd_path_is_direct_open(path: str, ext: Any, tile_index: int = 0) -> bool:
    """런타임 복제·래퍼 없이 보조 타일에서 바로 open 가능한 USD 경로."""
    p = str(path or "").strip()
    if not p:
        return False
    aux = resolve_split_aux_usd_path(ext, tile_index)
    if not aux:
        return False
    try:
        a = os.path.normcase(os.path.normpath(aux))
        b = os.path.normcase(os.path.normpath(p))
        if a == b:
            return True
        alt = resolve_local_data_path(p)
        if alt and os.path.normcase(os.path.normpath(alt)) == a:
            return True
    except Exception:
        pass
    return False


def split_dual_usd_paths_enabled(ext: Any = None) -> bool:
    """화면1·화면2 각각 다른 Master 파일 경로를 쓰는 모드."""
    _ = ext
    return resolve_split_aux_usd_path(ext, 0) is not None


def resolve_split_master_usd_path(ext: Any) -> Optional[str]:
    """분할 복제·래퍼에 쓸 Master USD 경로(절대/URL 우선)."""
    raw = ""
    try:
        raw = str(getattr(ext, "_tbs_last_loaded_usd_path", "") or "").strip()
    except Exception:
        raw = ""
    if raw:
        resolved = resolve_local_data_path(raw) or raw
        return str(resolved).strip() or None
    try:
        from .prim_utils import get_stage

        st = get_stage()
        if st is None:
            return None
        lyr = st.GetRootLayer()
        if lyr is None:
            return None
        p = getattr(lyr, "realPath", None) or lyr.identifier
        s = str(p or "").strip()
        if not s:
            return None
        return resolve_local_data_path(s) or s
    except Exception:
        return None


def usd_path_for_omni_client(path: str) -> str:
    """``omni.client.copy_async`` / ``open_stage`` 에 넘길 URI."""
    p = str(path or "").strip()
    if not p:
        return p
    low = p.lower()
    if low.startswith("omniverse://") or low.startswith("http://") or low.startswith("https://"):
        return p
    if p.startswith("file:"):
        return p
    try:
        return Path(p).resolve().as_uri()
    except Exception:
        return p


def _main_master_stage(ext: Any) -> Any:
    try:
        win = getattr(ext, "_tbs_usd_window", None)
        master = getattr(win, "_master", None) if win is not None else None
        if master is not None:
            return master.get_stage()
    except Exception:
        pass
    try:
        import omni.usd as ou

        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def _main_stage_cache_key(ext: Any) -> str:
    try:
        st = _main_master_stage(ext)
        if st is None:
            return ""
        lyr = st.GetRootLayer()
        if lyr is None:
            return ""
        ident = str(getattr(lyr, "identifier", "") or getattr(lyr, "realPath", "") or "")
        try:
            rev = int(st.GetRootLayer().GetCurrentRevision())
        except Exception:
            rev = 0
        try:
            inst_n = len(getattr(ext, "_tbs_registry", None).all_instances()) if getattr(ext, "_tbs_registry", None) else 0
        except Exception:
            inst_n = 0
        return f"{ident}|r{rev}|i{inst_n}"
    except Exception:
        return ""


def get_or_export_main_composed_stage(ext: Any, token: int, tile_index: int = 0) -> Optional[str]:
    """분할 1회당 Flatten+Export 를 한 번만 수행하고 보조 타일이 경로를 공유한다."""
    cache_key = _main_stage_cache_key(ext)
    try:
        cached_key = str(getattr(ext, "_tbs_split_composed_export_cache_key", "") or "")
        cached_tok = int(getattr(ext, "_tbs_split_composed_export_token", -1) or -1)
        cached_path = str(getattr(ext, "_tbs_split_composed_export_path", "") or "").strip()
        if (
            cache_key
            and cached_key == cache_key
            and cached_tok == int(token)
            and cached_path
            and os.path.isfile(cached_path)
        ):
            return cached_path
        if cache_key and cached_key == cached_key and cached_path and os.path.isfile(cached_path):
            return cached_path
    except Exception:
        pass
    path = export_main_composed_stage_to_temp(ext, token, tile_index)
    if path:
        try:
            ext._tbs_split_composed_export_path = path
            ext._tbs_split_composed_export_token = int(token)
            ext._tbs_split_composed_export_cache_key = cache_key
        except Exception:
            pass
    return path


def clear_split_composed_export_cache(ext: Any) -> None:
    try:
        ext._tbs_split_composed_export_path = None
        ext._tbs_split_composed_export_token = None
        ext._tbs_split_composed_export_cache_key = None
        ext._tbs_split_composed_prewarm_inflight = False
    except Exception:
        pass


def ext_has_composed_instances(ext: Any) -> bool:
    """TBS Load 후 /World 합성 인스턴스가 registry·master sublayer 에 있는지."""
    try:
        reg = getattr(ext, "_tbs_registry", None)
        if reg is not None and len(list(reg.all_instances())) > 0:
            return True
    except Exception:
        pass
    try:
        win = getattr(ext, "_tbs_usd_window", None)
        master = getattr(win, "_master", None) if win is not None else None
        if master is not None:
            subs = getattr(master, "_inst_sublayers", None)
            if isinstance(subs, dict) and len(subs) > 0:
                return True
            reg2 = getattr(win, "_registry", None)
            if reg2 is not None and reg2 is not reg and len(list(reg2.all_instances())) > 0:
                return True
    except Exception:
        pass
    try:
        st = _main_master_stage(ext)
        if st is not None:
            world = st.GetPrimAtPath("/World")
            if world and world.IsValid():
                for child in world.GetChildren():
                    pp = str(child.GetPath() or "")
                    if pp.startswith("/World/aaa"):
                        return True
                    try:
                        if child.GetReferences() or child.GetPayloads():
                            return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False


def composed_split_snapshot_ready(ext: Any) -> bool:
    """백그라운드 Flatten 스냅샷이 현재 메인 스테이지와 일치하는지."""
    try:
        cached_key = str(getattr(ext, "_tbs_split_composed_export_cache_key", "") or "").strip()
        cached_path = str(getattr(ext, "_tbs_split_composed_export_path", "") or "").strip()
        if not cached_key or not cached_path or not os.path.isfile(cached_path):
            return False
        return cached_key == str(_main_stage_cache_key(ext) or "").strip()
    except Exception:
        return False


async def resolve_composed_snapshot_for_split_async(
    ext: Any,
    token: int,
    *,
    max_wait_frames: int = 360,
) -> Optional[str]:
    """
    분할 bg load — prewarm 스냅샷 대기 후, 없으면 Flatten 1회(백그라운드).

    분할 UI 는 이미 떠 있으므로 여기서 Flatten 해도 체감 지연은 작다.
    """
    ready_path = str(getattr(ext, "_tbs_split_composed_export_path", "") or "").strip()
    if composed_split_snapshot_ready(ext) and ready_path:
        return ready_path

    try:
        import omni.kit.app as kit_app
    except Exception:
        kit_app = None  # type: ignore

    waited = 0
    while waited < max(1, int(max_wait_frames)):
        if composed_split_snapshot_ready(ext):
            p = str(getattr(ext, "_tbs_split_composed_export_path", "") or "").strip()
            if p and os.path.isfile(p):
                return p
        inflight = bool(getattr(ext, "_tbs_split_composed_prewarm_inflight", False))
        if not inflight and waited > 8:
            break
        if kit_app is not None:
            try:
                await kit_app.get_app().next_update_async()
            except Exception:
                break
        waited += 1

    if not ext_has_composed_instances(ext):
        return None

    if composed_split_snapshot_ready(ext):
        p = str(getattr(ext, "_tbs_split_composed_export_path", "") or "").strip()
        return p if p and os.path.isfile(p) else None

    if not bool(getattr(ext, "_tbs_split_composed_prewarm_inflight", False)):
        schedule_split_composed_snapshot_prewarm(ext)
        for _ in range(min(120, max_wait_frames)):
            if composed_split_snapshot_ready(ext):
                p = str(getattr(ext, "_tbs_split_composed_export_path", "") or "").strip()
                if p and os.path.isfile(p):
                    return p
            if not bool(getattr(ext, "_tbs_split_composed_prewarm_inflight", False)):
                break
            if kit_app is not None:
                try:
                    await kit_app.get_app().next_update_async()
                except Exception:
                    break

    return get_or_export_main_composed_stage(ext, int(token), 0)


def schedule_split_composed_snapshot_prewarm(ext: Any) -> None:
    """
    TBS Load 직후 백그라운드에서 Flatten 스냅샷을 1회 만든다.

    분할 시점에 동기 Flatten(수 초) 대신, 준비된 스냅샷을 ``copy_async`` 로 복제하면
    **빠르면서도 인스턴스 geometry 가 포함**된다.
    """
    if not ext_has_composed_instances(ext):
        return
    if composed_split_snapshot_ready(ext):
        return
    if bool(getattr(ext, "_tbs_split_composed_prewarm_inflight", False)):
        return
    try:
        ext._tbs_split_composed_prewarm_inflight = True
    except Exception:
        pass

    async def _go() -> None:
        try:
            import omni.kit.app as kit_app

            for _ in range(48):
                await kit_app.get_app().next_update_async()
            if not ext_has_composed_instances(ext):
                return
            register_main_composed_runtime(ext)
            main_rt = get_split_runtime_for_screen(ext, 1)
            _sync_main_mirror_state_before_replicate(main_rt)
            for _ in range(8):
                await kit_app.get_app().next_update_async()
            path = get_or_export_main_composed_stage(ext, 0, 0)
            if path:
                try:
                    print(
                        f"{_PRINT_PREFIX} composed snapshot prewarm OK path={path}",
                        flush=True,
                    )
                except Exception:
                    pass
        except Exception as exc:
            try:
                print(f"{_PRINT_PREFIX} composed snapshot prewarm failed: {exc}", flush=True)
            except Exception:
                pass
        finally:
            try:
                ext._tbs_split_composed_prewarm_inflight = False
            except Exception:
                pass

    try:
        import asyncio

        asyncio.ensure_future(_go())
    except Exception:
        try:
            ext._tbs_split_composed_prewarm_inflight = False
        except Exception:
            pass


def _sync_main_mirror_state_before_replicate(main_rt: Optional[SplitScreenRuntime]) -> None:
    """
    보조 타일 replicate 직전 — 메인 evaluator 가 inst sublayer 에 mirror 를 쓴 뒤 복제한다.

    빠른 clone 경로는 Flatten 없이 sublayer ``TransferContent`` 에 의존하는데,
    메인 mirror 가 sublayer 에 아직 기록되지 않은 채 복제하면 보조 타일이 비어 보인다.
    """
    if main_rt is None:
        return
    try:
        instances = list(main_rt.registry.all_instances())
    except Exception:
        instances = []
    for inst in instances:
        pp = str(getattr(inst, "prim_path", "") or "").strip()
        if not pp:
            continue
        try:
            main_rt.evaluator.begin_replay_mode(pp)
        except Exception:
            pass
        try:
            main_rt.evaluator.evaluate_instance_now(pp)
        except Exception:
            pass


def export_main_composed_stage_to_temp(ext: Any, token: int, tile_index: int) -> Optional[str]:
    """
    메인(default) stage 의 **현재 composed 결과**(session sublayer·Extract 포함)를
    임시 ``.usda`` 로 내보낸다. 보조 타일은 원본 master 파일 복제 대신 이 스냅샷을 연다.
    """
    register_main_composed_runtime(ext)
    main_rt = get_split_runtime_for_screen(ext, 1)
    _sync_main_mirror_state_before_replicate(main_rt)
    stage = _main_master_stage(ext)
    if stage is None:
        print(f"{_PRINT_PREFIX} export composed: main stage 없음", flush=True)
        return None
    try:
        flat = stage.Flatten()
        if flat is None:
            print(f"{_PRINT_PREFIX} export composed: Flatten() None", flush=True)
            return None
        path = os.path.normpath(
            os.path.join(
                tempfile.gettempdir(),
                f"morph_tbs_composed_aux_{tile_index}_{token}_{os.getpid()}.usda",
            )
        )
        if flat.Export(path) is False:
            print(f"{_PRINT_PREFIX} export composed: Export False path={path}", flush=True)
            return None
        try:
            sz = os.path.getsize(path)
        except Exception:
            sz = -1
        print(f"{_PRINT_PREFIX} export composed OK path={path} bytes={sz}", flush=True)
        return path
    except Exception as exc:
        print(f"{_PRINT_PREFIX} export composed failed: {exc}", flush=True)
        return None


def _runtime_map(ext: Any) -> Dict[str, SplitScreenRuntime]:
    m = getattr(ext, "_tbs_split_runtime_by_screen", None)
    if not isinstance(m, dict):
        m = {}
        ext._tbs_split_runtime_by_screen = m
    return m


def register_main_composed_runtime(ext: Any) -> None:
    """화면1 — TbsUsdWindow 가 이미 연 합성 Master 를 분할 런타임 맵에 등록."""
    try:
        win = getattr(ext, "_tbs_usd_window", None)
        master = getattr(win, "_master", None) if win is not None else None
        reg = getattr(ext, "_tbs_registry", None)
        ev = getattr(ext, "_tbs_evaluator", None)
        sch = getattr(ext, "_tbs_scheduler", None)
        if master is None or reg is None or ev is None or sch is None:
            return
        _runtime_map(ext)["1"] = SplitScreenRuntime(
            screen=1,
            context_name=None,
            master=master,
            registry=reg,
            evaluator=ev,
            scheduler=sch,
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} register_main failed: {exc}", flush=True)


def get_split_runtime_for_screen(ext: Any, screen: int) -> Optional[SplitScreenRuntime]:
    try:
        si = max(1, int(screen))
    except Exception:
        si = 1
    rt = _runtime_map(ext).get(str(si))
    if rt is not None:
        return rt
    if si == 1:
        register_main_composed_runtime(ext)
        return _runtime_map(ext).get("1")
    return None


def get_split_runtime_for_usd_context(ext: Any, usd_context_name: Optional[str]) -> Optional[SplitScreenRuntime]:
    cn = str(usd_context_name or "").strip()
    if not cn:
        return get_split_runtime_for_screen(ext, 1)
    try:
        names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    except Exception:
        names = []
    for i, nm in enumerate(names):
        if str(nm or "").strip() == cn:
            return get_split_runtime_for_screen(ext, i + 2)
    return None


def release_split_runtime_for_screen(ext: Any, screen_1based: int) -> None:
    """단일 보조 화면(2~) 런타임만 정리 — 분할 수 축소 시 재사용 타일을 유지하기 위함."""
    try:
        si = max(2, int(screen_1based))
    except Exception:
        return
    if si <= 0:
        return
    rt = _runtime_map(ext).pop(str(si), None)
    if rt is None:
        return
    try:
        rt.evaluator.stop()
    except Exception:
        pass
    try:
        rt.master.clear_all_inst_sublayers()
    except Exception:
        pass


def release_aux_split_runtimes(ext: Any, *, keep_screen_1: bool = True) -> None:
    """보조 화면(2~) evaluator 정리. 분할 해제·재빌드 전 호출."""
    m = _runtime_map(ext)
    for key in list(m.keys()):
        if keep_screen_1 and str(key) == "1":
            continue
        rt = m.pop(key, None)
        if rt is None:
            continue
        try:
            rt.evaluator.stop()
        except Exception:
            pass
        try:
            rt.master.clear_all_inst_sublayers()
        except Exception:
            pass


def _duplicate_sdf_layer(src_layer: Any) -> Optional[Any]:
    if src_layer is None:
        return None
    try:
        from pxr import Sdf  # type: ignore

        dst = Sdf.Layer.CreateAnonymous("tbs_split_layer_dup")
        dst.TransferContent(src_layer)
        return dst
    except Exception as exc:
        print(f"{_PRINT_PREFIX} duplicate_sdf_layer failed: {exc}", flush=True)
        return None


def _baked_layer_from_main_runtime(main_evaluator: Any, prim_path: str) -> Optional[Any]:
    rt_map = getattr(main_evaluator, "_runtime_by_path", None)
    if not isinstance(rt_map, dict):
        return None
    rt = rt_map.get(prim_path)
    if rt is None:
        return None
    off = getattr(rt, "_offscreen_stage", None)
    if off is None:
        return None
    try:
        return _duplicate_sdf_layer(off.GetRootLayer())
    except Exception:
        return None


def _install_registry_copy(aux_registry: AnimationInstanceRegistry, main_instances: List[AnimationInstance]) -> None:
    with aux_registry._lock:
        aux_registry._by_prim.clear()
        for inst in main_instances:
            pp = str(getattr(inst, "prim_path", "") or "").strip()
            if pp:
                aux_registry._by_prim[pp] = deepcopy(inst)


def _sync_aux_registry_metadata_from_main(
    main_registry: AnimationInstanceRegistry,
    aux_registry: AnimationInstanceRegistry,
) -> int:
    """듀얼 USD 경로: 보조 화면 Discover 후 화면1과 동일한 ``source_asset``·시간 메타를 맞춘다."""
    touched = 0
    for aux_inst in aux_registry.all_instances():
        pp = str(getattr(aux_inst, "prim_path", "") or "").strip()
        if not pp:
            continue
        main_inst = main_registry.get_by_prim_path(pp)
        if main_inst is None:
            continue
        for attr in (
            "source_asset",
            "mirror_root_prim_path",
            "asset_start_time",
            "asset_end_time",
            "asset_tps",
            "asset_kind",
            "instance_id",
            "baked",
        ):
            try:
                val = getattr(main_inst, attr, None)
            except Exception:
                continue
            if val is None or val == "" or val == 0:
                continue
            try:
                if getattr(aux_inst, attr, None) != val:
                    setattr(aux_inst, attr, val)
                    touched += 1
            except Exception:
                pass
    return touched


def _replicate_inst_sublayer(main_master: MasterStage, aux_master: MasterStage, prim_path: str) -> bool:
    src = main_master.get_inst_sublayer(prim_path)
    if src is None:
        return False
    dst = aux_master.ensure_inst_sublayer(prim_path, tag_hint=prim_path)
    if dst is None:
        return False
    try:
        dst.TransferContent(src)
        return True
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} replicate inst_sublayer failed prim={prim_path}: {exc}",
            flush=True,
        )
        return False


def _replicate_all_inst_sublayers(main_master: MasterStage, aux_master: MasterStage) -> int:
    n = 0
    try:
        keys = list(getattr(main_master, "_inst_sublayers", {}).keys())
    except Exception:
        keys = []
    for pp in keys:
        if _replicate_inst_sublayer(main_master, aux_master, str(pp)):
            n += 1
    return n


def _extract_one(
    evaluator: RuntimeEvaluator,
    prim_path: str,
    *,
    log_tag: str,
) -> bool:
    pp = str(prim_path or "").strip()
    if not pp:
        return False
    try:
        evaluator.end_replay_mode(pp)
    except Exception:
        pass
    try:
        result = evaluator.extract_and_attach_from_master(
            pp,
            source_asset_for_log=f"<{log_tag}:{pp}>",
        )
        return result is not None and bool(getattr(result, "ok", False))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} extract failed {pp}: {exc}", flush=True)
        return False


def _attach_from_main_baked(
    aux_evaluator: RuntimeEvaluator,
    main_evaluator: Any,
    inst: AnimationInstance,
) -> bool:
    pp = str(getattr(inst, "prim_path", "") or "").strip()
    layer = _baked_layer_from_main_runtime(main_evaluator, pp)
    if layer is None:
        return False
    hint = str(getattr(inst, "mirror_root_prim_path", "") or getattr(inst, "source_asset", "") or "").strip()
    try:
        ok = aux_evaluator.attach_memory_baked_layer(
            pp,
            layer,
            source_asset_for_log=f"<split-replicate:{inst.instance_id}>",
            mirror_asset_path_hint=hint,
        )
        if ok:
            try:
                aux_inst = aux_evaluator._registry.get_by_prim_path(pp)
                if aux_inst is not None:
                    aux_inst.baked = True
            except Exception:
                pass
        return bool(ok)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} attach_from_main_baked failed {pp}: {exc}", flush=True)
        return False


def _refresh_aux_viewport_resolution(
    win_name: str, *, ext: Any = None, split_n: int = 0
) -> None:
    """hydrate·mirror write 직후 보조 타일 렌더 버퍼/입력을 다시 맞춘다."""
    wn = str(win_name or "").strip()
    if not wn:
        return
    try:
        from .sim_multi_view import (
            channel_count_for_split,
            refresh_split_viewport_resolution_from_grid,
        )

        sn = int(split_n) if split_n else 0
        if sn <= 1 and ext is not None:
            try:
                sn = channel_count_for_split(
                    int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
                )
            except Exception:
                sn = 2
        if sn > 1:
            try:
                from .sim_multi_view_widget import is_split_widget_layout_active, sync_split_widget_fill_frame, sync_split_widget_aux_render

                if ext is not None and is_split_widget_layout_active(ext):
                    sync_split_widget_fill_frame(ext, sn)
                    sync_split_widget_aux_render(ext)
                    return
            except Exception:
                pass
            refresh_split_viewport_resolution_from_grid(
                wn, sn, ext=ext, force_window_rect=not bool(
                    getattr(ext, "_tbs_split_used_dock_layout", False)
                ),
            )
            return
    except Exception:
        pass
    try:
        import omni.ui as ui
        from omni.kit.viewport.utility import get_viewport_from_window_name

        w = ui.Workspace.get_window(wn)
        api = get_viewport_from_window_name(wn)
        if w is not None and api is not None and hasattr(api, "resolution"):
            ww = int(getattr(w, "width", 0) or 0)
            hh = int(getattr(w, "height", 0) or 0)
            if ww >= 8 and hh >= 8:
                api.resolution = (max(1, ww), max(1, hh))
        if api is not None and hasattr(api, "fill_frame"):
            api.fill_frame = True
    except Exception:
        pass


def _activate_aux_split_display(
    aux_evaluator: Any,
    main_evaluator: Optional[Any] = None,
    *,
    aux_win_name: str = "",
    ext: Any = None,
) -> int:
    """
    보조 타일 evaluator 가 master mirror 에 default 를 쓰도록 replay 를 켠다.

    ``evaluate_instance_now`` / ``_option_e_evaluate_instance`` 는
    ``_evaluator_active_prims`` 에 없으면 **0 write** 로 조용히 실패한다.
    (로그상 attach OK 인데 viewport 가 비어 보이는 주 원인)
    """
    wrote_total = 0
    try:
        instances = list(aux_evaluator._registry.all_instances())
    except Exception:
        instances = []
    for inst in instances:
        pp = str(getattr(inst, "prim_path", "") or "").strip()
        if not pp:
            continue
        rt = None
        try:
            rt = aux_evaluator._runtime_by_path.get(pp)
            if rt is not None:
                if aux_evaluator._master is not None:
                    try:
                        rt.set_master_stage(aux_evaluator._master.get_stage())
                    except Exception:
                        pass
                rt.setup_master_mirror_prim()
        except Exception:
            pass
        try:
            aux_evaluator.begin_replay_mode(pp)
        except Exception:
            pass
        try:
            wrote = int(aux_evaluator.evaluate_instance_now(pp))
            if wrote > 0:
                wrote_total += 1
            elif rt is not None and not rt.is_ready:
                try:
                    print(
                        f"{_PRINT_PREFIX} aux display NOT_READY prim={pp} "
                        f"offscreen={bool(getattr(rt, '_offscreen_stage', None))} "
                        f"master={bool(getattr(rt, '_master_stage', None))}",
                        flush=True,
                    )
                except Exception:
                    pass
        except Exception:
            pass
    wn = str(aux_win_name or "").strip()
    if wn:
        _refresh_aux_viewport_resolution(wn, ext=ext)
    try:
        print(
            f"{_PRINT_PREFIX} aux display activate: instances={len(instances)} "
            f"mirror_writes={wrote_total}",
            flush=True,
        )
    except Exception:
        pass
    return wrote_total


def hydrate_split_screen_composed_stage(
    ext: Any,
    ctx_name: str,
    screen_1based: int,
    *,
    fast_visual: bool = False,
) -> Optional[SplitScreenRuntime]:
    """
    보조 USD 컨텍스트에 화면1과 동일한 합성 인스턴스·런타임을 구성한다.
    """
    cn = str(ctx_name or "").strip()
    if not cn:
        return None
    try:
        si = max(2, int(screen_1based))
    except Exception:
        si = 2

    register_main_composed_runtime(ext)
    independent_aux = split_dual_usd_paths_enabled(ext)
    # 듀얼 경로에서도 화면1 registry·baked 런타임을 메타/attach 동기화에 사용한다.
    main_rt = get_split_runtime_for_screen(ext, 1)

    master = MasterStage(context_name=cn)
    if not master.bind_to_existing_context(cn):
        print(f"{_PRINT_PREFIX} screen{si}: no stage on ctx={cn}", flush=True)
        return None
    try:
        master.set_root_layer_edit_target()
        master.force_fixed_fps_30()
    except Exception:
        pass

    registry = AnimationInstanceRegistry()
    evaluator = RuntimeEvaluator(registry)
    evaluator.set_master(master)
    try:
        evaluator.start()
    except Exception:
        pass
    scheduler = PlaybackScheduler(registry=registry, evaluator=evaluator)

    main_instances: List[AnimationInstance] = []
    if main_rt is not None:
        try:
            main_instances = list(main_rt.registry.all_instances())
        except Exception:
            main_instances = []

    replicated = 0
    extracted = 0
    sublayers = 0

    if independent_aux:
        main_instances = []
        print(
            f"{_PRINT_PREFIX} screen{si} dual-path: 독립 Discover+Extract "
            f"(화면1 메타·baked 동기화)",
            flush=True,
        )
        try:
            added = CompositionDiscovery(master, registry).discover()
            print(
                f"{_PRINT_PREFIX} screen{si} discover added={len(added)} (dual-path)",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} screen{si} discover failed: {exc}", flush=True)
        if main_rt is not None:
            try:
                n_meta = _sync_aux_registry_metadata_from_main(main_rt.registry, registry)
                print(
                    f"{_PRINT_PREFIX} screen{si} sync metadata from screen1: "
                    f"fields={n_meta}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} screen{si} sync metadata failed: {exc}",
                    flush=True,
                )
        extract_pps: List[str] = []
        for inst in registry.all_instances():
            pp = str(getattr(inst, "prim_path", "") or "").strip()
            if not pp:
                continue
            ok_rep = False
            if main_rt is not None:
                main_inst = main_rt.registry.get_by_prim_path(pp)
                if main_inst is not None:
                    ok_rep = _attach_from_main_baked(
                        evaluator, main_rt.evaluator, main_inst
                    )
            if ok_rep:
                replicated += 1
            else:
                extract_pps.append(pp)
        if extract_pps:
            try:
                from .tbs_extract_from_master import master_flatten_cache

                stage = master.get_stage()
            except Exception:
                master_flatten_cache = None  # type: ignore
                stage = None
            if callable(master_flatten_cache) and stage is not None:
                with master_flatten_cache(stage):
                    for pp in extract_pps:
                        if _extract_one(
                            evaluator, pp, log_tag=f"split-extract-{si}"
                        ):
                            extracted += 1
            else:
                for pp in extract_pps:
                    if _extract_one(
                        evaluator, pp, log_tag=f"split-extract-{si}"
                    ):
                        extracted += 1

    if (not independent_aux) and main_instances and main_rt is not None and main_rt.master is not None:
        _sync_main_mirror_state_before_replicate(main_rt)
        _install_registry_copy(registry, main_instances)
        sublayers = _replicate_all_inst_sublayers(main_rt.master, master)
        print(
            f"{_PRINT_PREFIX} screen{si} replicate from main: "
            f"instances={len(main_instances)} inst_sublayers={sublayers}",
            flush=True,
        )
        extract_fail: List[str] = []
        for inst in main_instances:
            pp = str(getattr(inst, "prim_path", "") or "").strip()
            if not pp:
                continue
            _replicate_inst_sublayer(main_rt.master, master, pp)
            ok_rep = _attach_from_main_baked(evaluator, main_rt.evaluator, inst)
            if not ok_rep:
                extract_fail.append(pp)
            else:
                replicated += 1
        if extract_fail:
            try:
                from .tbs_extract_from_master import master_flatten_cache

                _st = master.get_stage()
            except Exception:
                master_flatten_cache = None  # type: ignore
                _st = None
            if callable(master_flatten_cache) and _st is not None:
                with master_flatten_cache(_st):
                    for pp in extract_fail:
                        if _extract_one(
                            evaluator, pp, log_tag=f"split-extract-{si}"
                        ):
                            extracted += 1
            else:
                for pp in extract_fail:
                    if _extract_one(
                        evaluator, pp, log_tag=f"split-extract-{si}"
                    ):
                        extracted += 1
    elif not independent_aux:
        try:
            added = CompositionDiscovery(master, registry).discover()
            print(
                f"{_PRINT_PREFIX} screen{si} discover added={len(added)} (main instances 없음)",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} screen{si} discover failed: {exc}", flush=True)
        extract_pps2 = [
            str(getattr(inst, "prim_path", "") or "").strip()
            for inst in registry.all_instances()
            if str(getattr(inst, "prim_path", "") or "").strip()
        ]
        if extract_pps2:
            try:
                from .tbs_extract_from_master import master_flatten_cache

                _st2 = master.get_stage()
            except Exception:
                master_flatten_cache = None  # type: ignore
                _st2 = None
            if callable(master_flatten_cache) and _st2 is not None:
                with master_flatten_cache(_st2):
                    for pp in extract_pps2:
                        if _extract_one(
                            evaluator, pp, log_tag=f"split-extract-{si}"
                        ):
                            extracted += 1
            else:
                for pp in extract_pps2:
                    if _extract_one(
                        evaluator, pp, log_tag=f"split-extract-{si}"
                    ):
                        extracted += 1

    aux_wn = f"TBS_SimSplit_{max(1, int(si) - 1)}"
    wrote = _activate_aux_split_display(
        evaluator,
        main_rt.evaluator if main_rt is not None else None,
        aux_win_name=aux_wn,
        ext=ext,
    )

    aux_inst_count = len(list(registry.all_instances()))
    if main_rt is not None and wrote < aux_inst_count:
        retry_pps: List[str] = []
        for inst in registry.all_instances():
            pp = str(getattr(inst, "prim_path", "") or "").strip()
            if not pp:
                continue
            if not independent_aux and main_instances and main_rt.master is not None:
                _replicate_inst_sublayer(main_rt.master, master, pp)
            retry_pps.append(pp)
        if retry_pps:
            try:
                from .tbs_extract_from_master import master_flatten_cache

                _st3 = master.get_stage()
            except Exception:
                master_flatten_cache = None  # type: ignore
                _st3 = None
            if callable(master_flatten_cache) and _st3 is not None:
                with master_flatten_cache(_st3):
                    for pp in retry_pps:
                        if _extract_one(
                            evaluator, pp, log_tag=f"split-extract-retry-{si}"
                        ):
                            extracted += 1
            else:
                for pp in retry_pps:
                    if _extract_one(
                        evaluator, pp, log_tag=f"split-extract-retry-{si}"
                    ):
                        extracted += 1
        wrote = _activate_aux_split_display(
            evaluator,
            main_rt.evaluator,
            aux_win_name=aux_wn,
            ext=ext,
        )

    print(
        f"{_PRINT_PREFIX} screen{si} ctx={cn} "
        f"replicated={replicated} extracted={extracted} sublayers={sublayers} "
        f"total={len(registry.all_instances())} mirror_writes={wrote}",
        flush=True,
    )

    rt = SplitScreenRuntime(
        screen=si,
        context_name=cn,
        master=master,
        registry=registry,
        evaluator=evaluator,
        scheduler=scheduler,
    )
    _runtime_map(ext)[str(si)] = rt
    try:
        from . import sim_multi_diag as _mdiag

        _mdiag.log_runtime_registered(ext, si)
    except Exception:
        pass
    return rt


async def hydrate_split_screen_composed_stage_async(
    ext: Any,
    ctx_name: str,
    screen_1based: int,
    *,
    settle_frames: int = 2,
    fast_visual: bool = False,
) -> Optional[SplitScreenRuntime]:
    """스테이지 composition 이 안정된 뒤 hydrate."""
    try:
        import omni.kit.app as kit_app

        for _ in range(max(1, int(settle_frames))):
            await kit_app.get_app().next_update_async()
    except Exception:
        pass
    rt = hydrate_split_screen_composed_stage(
        ext,
        ctx_name,
        screen_1based,
        fast_visual=fast_visual,
    )
    try:
        import omni.kit.app as kit_app

        for _ in range(2 if fast_visual else 4):
            await kit_app.get_app().next_update_async()
        if rt is not None:
            try:
                si = max(2, int(screen_1based))
            except Exception:
                si = 2
            _activate_aux_split_display(
                rt.evaluator,
                None,
                aux_win_name=f"TBS_SimSplit_{max(1, si - 1)}",
                ext=ext,
            )
    except Exception:
        pass
    return rt


__all__ = [
    "SplitScreenRuntime",
    "aux_usd_path_is_direct_open",
    "clear_split_composed_export_cache",
    "composed_split_snapshot_ready",
    "resolve_composed_snapshot_for_split_async",
    "export_main_composed_stage_to_temp",
    "ext_has_composed_instances",
    "get_or_export_main_composed_stage",
    "get_split_runtime_for_screen",
    "get_split_runtime_for_usd_context",
    "hydrate_split_screen_composed_stage",
    "hydrate_split_screen_composed_stage_async",
    "register_main_composed_runtime",
    "release_aux_split_runtimes",
    "release_split_runtime_for_screen",
    "resolve_split_aux_usd_path",
    "resolve_split_master_usd_path",
    "schedule_split_composed_snapshot_prewarm",
    "split_dual_usd_paths_enabled",
    "usd_path_for_omni_client",
]

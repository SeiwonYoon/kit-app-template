"""장비 USD 자동 로드 — EP 포트 수에 따라 ``data/usd`` 합성 파일을 연다."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .tbs_data_paths import resolve_local_data_path

_PRINT_PREFIX = "[TBS/Autoload]"

# Kit 시작 시·EP 2개(콤보 idx 0) → 1번, EP 3개(idx 1) → 2번
load_automatically = True

# ``extension_data_root()`` 기준 상대 경로 (lam_control 과 동일)
default_usd_path_ep2 = "usd/test1.usd"
default_usd_path_ep3 = "usd/test2.usd"


def ep_count_from_combo_idx(idx: int) -> int:
    """시뮬 EP 콤보: 0 → EP2, 1 → EP3."""
    return 3 if int(idx) == 1 else 2


def relative_usd_path_for_ep_count(ep_count: int) -> str:
    return default_usd_path_ep3 if int(ep_count) >= 3 else default_usd_path_ep2


def resolve_equipment_usd_for_ep_count(ep_count: int) -> Optional[str]:
    rel = relative_usd_path_for_ep_count(ep_count)
    return resolve_local_data_path(rel)


async def open_equipment_usd_path(ext: Any, resolved_path: str) -> None:
    """Master open + Discover + auto-Extract (``TbsUsdWindow``)."""
    path = (resolved_path or "").strip()
    if not path:
        return

    from .usd_loader_utils import path_has_supported_stage_extension

    if not path_has_supported_stage_extension(path):
        print(f"{_PRINT_PREFIX} unsupported extension: {path}", flush=True)
        return

    import omni.client

    try:
        result, _ = await asyncio.wait_for(omni.client.stat_async(path), timeout=1.5)
        if result != omni.client.Result.OK:
            print(f"{_PRINT_PREFIX} file not found: {path}", flush=True)
            return
    except Exception as exc:
        print(f"{_PRINT_PREFIX} stat failed: {path} ({exc})", flush=True)
        return

    win = getattr(ext, "_tbs_usd_window", None)
    if win is not None:
        try:
            ok = win.open_master_at_path(path, log_prefix="EP autoload")
            if not ok:
                print(f"{_PRINT_PREFIX} open_master failed: {path}", flush=True)
                return
        except Exception as exc:
            print(f"{_PRINT_PREFIX} open_master exception: {path} ({exc})", flush=True)
            return
    else:
        import omni.usd as ou

        ou.get_context().open_stage(path)
    try:
        if "https://" in path.lower() and not getattr(ext, "_tbs_mdl_https_texture_hint_done", False):
            ext._tbs_mdl_https_texture_hint_done = True
            print(
                "[TBS] https 원격 씬: MDL 본문에만 있는 https 텍스처는 RTX가 자주 못 풉니다. "
                "USD 속성(Asset/String/Token)으로 드러난 https 이미지는 로드 후 자동으로 로컬 캐시에 받아 덮어쓰며, "
                "약 1초 뒤 한 번 더 스캔합니다.",
                flush=True,
            )
    except Exception:
        pass
    try:
        ext._tbs_last_loaded_usd_path = path
        ext._tbs_multi_split_usd_ready = True
    except Exception:
        pass

    label = getattr(ext, "_load_status_label", None)
    if label is not None:
        try:
            label.text = "자동 로드 완료."
        except Exception:
            pass

    fn = getattr(ext, "_sync_sim_multi_split_row_visibility_fn", None)
    if callable(fn):
        try:
            fn(ext)
        except Exception:
            pass

    async def _sync_split_after_stage_settles() -> None:
        try:
            import omni.kit.app as kapp

            await kapp.get_app().next_update_async()
        except Exception:
            return
        f2 = getattr(ext, "_sync_sim_multi_split_row_visibility_fn", None)
        if callable(f2):
            try:
                f2(ext)
            except Exception:
                pass

    try:
        asyncio.ensure_future(_sync_split_after_stage_settles())
    except Exception:
        pass

    try:
        from .usd_https_asset_fixup import schedule_https_asset_fixup_if_applicable

        asyncio.ensure_future(schedule_https_asset_fixup_if_applicable(ext, path))
    except Exception:
        pass

    print(f"{_PRINT_PREFIX} opened: {path}", flush=True)


def _sync_load_path_field(ext: Any, resolved_path: str) -> None:
    m = getattr(ext, "_path_model", None)
    if m is None:
        return
    try:
        m.set_value_as_string(resolved_path)
    except Exception:
        try:
            m.set_value(resolved_path)
        except Exception:
            pass


async def autoload_equipment_usd_for_ep_count(
    ext: Any,
    ep_count: int,
    *,
    reason: str = "",
) -> None:
    if not load_automatically:
        return
    resolved = resolve_equipment_usd_for_ep_count(ep_count)
    rel = relative_usd_path_for_ep_count(ep_count)
    if not resolved:
        print(
            f"{_PRINT_PREFIX} skip EP={ep_count} — not found under data/: {rel} ({reason})",
            flush=True,
        )
        return
    _sync_load_path_field(ext, resolved)
    note = f" ({reason})" if reason else ""
    print(
        f"{_PRINT_PREFIX} EP={ep_count} → {rel}{note}",
        flush=True,
    )
    await open_equipment_usd_path(ext, resolved)


def request_equipment_autoload_for_ep_count(ext: Any, ep_count: int, *, reason: str = "") -> None:
    """메인 스레드·백그라운드 어디서든 호출 가능 — asyncio 로드 예약."""
    try:
        asyncio.ensure_future(autoload_equipment_usd_for_ep_count(ext, ep_count, reason=reason))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} schedule failed: {exc}", flush=True)


def on_sim_ep_count_combo_changed(ext: Any) -> None:
    """``on_sim_ep_count_changed`` 마지막에 호출 — EP 콤보 idx 변경 시에만 USD 교체."""
    if not load_automatically:
        return
    try:
        idx = int(ext._sim_ep_count_combo.model.get_item_value_model().as_int)
    except Exception:
        idx = 0
    last_idx = getattr(ext, "_equipment_autoload_combo_idx", None)
    if last_idx is not None and int(last_idx) == int(idx):
        return
    ext._equipment_autoload_combo_idx = int(idx)
    ep_count = ep_count_from_combo_idx(idx)
    request_equipment_autoload_for_ep_count(
        ext,
        ep_count,
        reason="ep_count_changed",
    )


def teardown_equipment_autoload(ext: Any) -> None:
    sub = getattr(ext, "_equipment_autoload_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
        ext._equipment_autoload_sub = None


__all__ = [
    "load_automatically",
    "default_usd_path_ep2",
    "default_usd_path_ep3",
    "ep_count_from_combo_idx",
    "relative_usd_path_for_ep_count",
    "resolve_equipment_usd_for_ep_count",
    "request_equipment_autoload_for_ep_count",
    "on_sim_ep_count_combo_changed",
    "teardown_equipment_autoload",
]

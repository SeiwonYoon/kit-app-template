"""Master USD open 후 듀얼 viewport 분할·화면2 로드 연동 (TBS control_window.notify_* 패턴)."""

from __future__ import annotations

import asyncio
from typing import Any


def notify_lam_composed_usd_ready_for_split(ext: Any, usd_path: str = "") -> None:
    """합성 Master USD open 성공 후 분할 런타임·화면2 USD 로드를 스케줄한다."""
    try:
        from . import lam_multi_viewport

        lam_multi_viewport.invalidate_split_layout_cache(ext)
    except Exception:
        pass
    p = str(usd_path or "").strip()
    if p:
        try:
            from .lam_data_paths import resolve_local_data_path

            resolved = resolve_local_data_path(p) or p
            ext._lam_last_loaded_usd_path = str(resolved).strip()
        except Exception:
            try:
                ext._lam_last_loaded_usd_path = p
            except Exception:
                pass
    try:
        from .lam_split_composed_loader import (
            register_main_composed_runtime,
            split_dual_usd_paths_enabled,
        )

        register_main_composed_runtime(ext)
    except Exception:
        pass
    try:
        from .lam_split_composed_loader import schedule_split_composed_snapshot_prewarm

        if not split_dual_usd_paths_enabled(ext):
            schedule_split_composed_snapshot_prewarm(ext)
    except Exception:
        pass
    try:
        ext._lam_multi_split_usd_ready = True
    except Exception:
        pass

    async def _sync_after_stage_settles() -> None:
        try:
            import omni.kit.app as kit_app

            # Master open + Discover/Extract 직후 한두 프레임으로는 부족할 수 있음
            for _ in range(4):
                await kit_app.get_app().next_update_async()
        except Exception:
            return
        try:
            from . import lam_multi_viewport

            if bool(getattr(ext, "_lam_split_deferred_aux_load_pending", False)):
                lam_multi_viewport.schedule_deferred_aux_usd_load_after_master(ext)
            else:
                lam_multi_viewport.schedule_split_rebuild_after_master_reload(ext)
        except Exception:
            pass

    try:
        asyncio.ensure_future(_sync_after_stage_settles())
    except Exception:
        pass


__all__ = ["notify_lam_composed_usd_ready_for_split"]

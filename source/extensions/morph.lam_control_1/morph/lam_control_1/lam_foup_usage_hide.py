"""파싱된 FOUP 사용 개수(1/2/3)에 따른 추가 prim 숨김.

``lot_id`` → ``foup_index`` 매핑 결과(dwell)로 사용 FOUP 개수를 세고,
``lam_sim_control_defaults`` 의 개수별 경로 목록을 자동 적용한다.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, List, Optional, Sequence

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/FoupUsageHide]"


def count_used_foups_from_dwells(dwells: Optional[Iterable[Any]]) -> int:
    """dwell 의 ``foup_index``(1~3) 고유 개수. 없으면 1."""
    used = set()
    for d in dwells or ():
        try:
            fi = int(getattr(d, "foup_index", 0) or 0)
        except Exception:
            continue
        if 1 <= fi <= 3:
            used.add(fi)
    n = len(used)
    if n <= 0:
        return 1
    return min(3, n)


def count_used_foups_from_lot_map(lot_map: Optional[dict]) -> int:
    """``build_lot_id_to_foup_index`` 결과 값(1~3) 고유 개수."""
    used = set()
    for v in dict(lot_map or {}).values():
        try:
            fi = int(v)
        except Exception:
            continue
        if 1 <= fi <= 3:
            used.add(fi)
    n = len(used)
    if n <= 0:
        return 1
    return min(3, n)


def _read_hide_lists() -> tuple[List[str], List[str], List[str]]:
    try:
        from . import lam_sim_control_defaults as d

        a = list(getattr(d, "FOUP_USAGE_EXTRA_HIDE_PRIMS_1", None) or [])
        b = list(getattr(d, "FOUP_USAGE_EXTRA_HIDE_PRIMS_2", None) or [])
        c = list(getattr(d, "FOUP_USAGE_EXTRA_HIDE_PRIMS_3", None) or [])
        return (
            [str(p).strip() for p in a if str(p).strip()],
            [str(p).strip() for p in b if str(p).strip()],
            [str(p).strip() for p in c if str(p).strip()],
        )
    except Exception:
        return ([], [], [])


def extra_hide_paths_for_foup_count(foup_count: int) -> List[str]:
    """사용 FOUP 개수(1/2/3)에 해당하는 추가 숨김 경로."""
    n = max(1, min(3, int(foup_count or 1)))
    a, b, c = _read_hide_lists()
    if n <= 1:
        return list(a)
    if n == 2:
        return list(b)
    return list(c)


def all_managed_extra_hide_paths() -> List[str]:
    """1/2/3 목록 합집합 (전환·복원 시 먼저 다시 보이게)."""
    a, b, c = _read_hide_lists()
    seen = set()
    out: List[str] = []
    for p in a + b + c:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _stage_for_context(usd_context_name: Optional[str]) -> Any:
    try:
        import omni.usd as ou  # type: ignore

        key = str(usd_context_name or "").strip()
        ctx = ou.get_context(key) if key else ou.get_context()
        if ctx is None:
            return None
        return ctx.get_stage()
    except Exception:
        return None


def _set_path_visible(stage: Any, path: str, visible: bool) -> bool:
    from pxr import UsdGeom  # type: ignore

    try:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return False
        img = UsdGeom.Imageable(prim)
        if not img:
            return False
        if visible:
            img.MakeVisible()
        else:
            img.MakeInvisible()
        return True
    except Exception as exc:
        print(
            f"{_PRINT_PREFIX} visibility fail path={path!r}: {exc}",
            flush=True,
        )
        return False


def _apply_on_stage(
    stage: Any,
    *,
    hide_paths: Sequence[str],
    restore_paths: Sequence[str],
) -> tuple[int, int]:
    shown = 0
    hidden = 0
    hide_set = {str(p).strip() for p in hide_paths if str(p).strip()}
    for p in restore_paths:
        if not p:
            continue
        # 이번에 숨길 목록은 restore 단계에서 건드리지 않음(바로 아래 hide)
        if p in hide_set:
            continue
        if _set_path_visible(stage, p, True):
            shown += 1
    for p in hide_paths:
        ps = str(p).strip()
        if not ps:
            continue
        if _set_path_visible(stage, ps, False):
            hidden += 1
    return shown, hidden


def apply_foup_usage_extra_hide_on_stage(
    stage: Any,
    *,
    dwells: Optional[Iterable[Any]] = None,
    foup_count: Optional[int] = None,
    screen: int = 1,
) -> int:
    """이미 main/stage 컨텍스트일 때 동기 적용. Returns foup_count."""
    si = max(1, int(screen))
    if foup_count is not None:
        n = max(1, min(3, int(foup_count)))
    else:
        n = count_used_foups_from_dwells(dwells)
    hide_paths = extra_hide_paths_for_foup_count(n)
    restore_paths = all_managed_extra_hide_paths()
    if stage is None:
        return n
    if not hide_paths and not restore_paths:
        print(
            f"{_PRINT_PREFIX} screen{si} foup_count={n} — hide lists empty, skip",
            flush=True,
        )
        return n
    shown, hidden = _apply_on_stage(
        stage, hide_paths=hide_paths, restore_paths=restore_paths
    )
    print(
        f"{_PRINT_PREFIX} screen{si} foup_count={n} "
        f"hide={hidden}/{len(hide_paths)} restore_other={shown}",
        flush=True,
    )
    return n


def restore_foup_usage_extra_hide_on_stage(
    stage: Any,
    *,
    screen: int = 1,
) -> None:
    """이미 main 일 때 — 개수별 숨김 목록 전체를 다시 보이게."""
    si = max(1, int(screen))
    paths = all_managed_extra_hide_paths()
    if stage is None or not paths:
        return
    ok = 0
    for p in paths:
        if _set_path_visible(stage, p, True):
            ok += 1
    print(
        f"{_PRINT_PREFIX} screen{si} restore visible {ok}/{len(paths)}",
        flush=True,
    )


def apply_foup_usage_extra_hide_for_playback(
    *,
    dwells: Optional[Iterable[Any]] = None,
    foup_count: Optional[int] = None,
    usd_context_name: Optional[str] = None,
    screen: int = 1,
    wait: bool = True,
    timeout_sec: float = 8.0,
) -> int:
    """파싱 결과로 FOUP 개수를 정하고 defaults 목록을 숨김 적용.

    Returns:
        적용에 사용한 foup_count (1~3). 목록이 비어도 판별값은 반환.
    """
    si = max(1, int(screen))
    if foup_count is not None:
        n = max(1, min(3, int(foup_count)))
    else:
        n = count_used_foups_from_dwells(dwells)
    hide_paths = extra_hide_paths_for_foup_count(n)
    restore_paths = all_managed_extra_hide_paths()
    ctx = str(usd_context_name or "").strip()

    if not hide_paths and not restore_paths:
        print(
            f"{_PRINT_PREFIX} screen{si} foup_count={n} — hide lists empty, skip",
            flush=True,
        )
        return n

    done = threading.Event()

    def _on_main() -> None:
        try:
            stage = _stage_for_context(ctx)
            apply_foup_usage_extra_hide_on_stage(
                stage,
                dwells=dwells,
                foup_count=n,
                screen=si,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} screen{si} apply failed: {exc}", flush=True)
        finally:
            done.set()

    schedule_on_main_thread(_on_main)
    if wait:
        done.wait(timeout=max(0.5, float(timeout_sec)))
    return n


def restore_foup_usage_extra_hide(
    *,
    usd_context_name: Optional[str] = None,
    screen: int = 1,
    wait: bool = True,
    timeout_sec: float = 8.0,
) -> None:
    """정지(초기화) 시 — 개수별 숨김 목록 전체를 다시 보이게."""
    si = max(1, int(screen))
    paths = all_managed_extra_hide_paths()
    if not paths:
        return
    ctx = str(usd_context_name or "").strip()
    done = threading.Event()

    def _on_main() -> None:
        try:
            stage = _stage_for_context(ctx)
            restore_foup_usage_extra_hide_on_stage(stage, screen=si)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} screen{si} restore failed: {exc}", flush=True)
        finally:
            done.set()

    schedule_on_main_thread(_on_main)
    if wait:
        done.wait(timeout=max(0.5, float(timeout_sec)))


__all__ = [
    "count_used_foups_from_dwells",
    "count_used_foups_from_lot_map",
    "extra_hide_paths_for_foup_count",
    "all_managed_extra_hide_paths",
    "apply_foup_usage_extra_hide_on_stage",
    "restore_foup_usage_extra_hide_on_stage",
    "apply_foup_usage_extra_hide_for_playback",
    "restore_foup_usage_extra_hide",
]

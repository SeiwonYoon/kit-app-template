"""파싱된 FOUP 사용 개수(1/2/3)에 따른 추가 prim 숨김.

``FOUP_USAGE_EXTRA_HIDE_PRIMS_1/2/3`` = 각각 FOUP1/2/3 관련 경로.
사용 개수 N 이면 ``_PRIMS_1..N`` 을 숨기고, 나머지(``N+1..3``)는 시작 시 강제 표시.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, List, Optional, Sequence

from .kit_main_dispatch import schedule_on_main_thread

_PRINT_PREFIX = "[LAM/FoupUsageHide]"


def count_used_foups_from_dwells(dwells: Optional[Iterable[Any]]) -> int:
    """dwell 기준 사용 FOUP 개수(1~3).

    ``foup_index`` 고유 개수와 ``lot_id`` 고유 개수 중 큰 값을 쓴다.
    (index 가 전부 1로만 남는 경우에도 lot 수로 2·3을 복구)
    """
    used_idx: set = set()
    used_lots: set = set()
    for d in dwells or ():
        try:
            fi = int(getattr(d, "foup_index", 0) or 0)
        except Exception:
            fi = 0
        if 1 <= fi <= 3:
            used_idx.add(fi)
        lid = str(getattr(d, "lot_id", "") or "").strip()
        if lid:
            used_lots.add(lid)
    n_idx = len(used_idx)
    n_lot = min(3, len(used_lots)) if used_lots else 0
    n = max(n_idx, n_lot)
    if n <= 0:
        return 1
    return min(3, n)


def count_used_foups_from_lot_map(lot_map: Optional[dict]) -> int:
    """``build_lot_id_to_foup_index`` 결과 — lot 수와 foup_index 값 중 큰 쪽."""
    m = dict(lot_map or {})
    used_idx: set = set()
    for v in m.values():
        try:
            fi = int(v)
        except Exception:
            continue
        if 1 <= fi <= 3:
            used_idx.add(fi)
    n_idx = len(used_idx)
    n_lot = min(3, len(m)) if m else 0
    n = max(n_idx, n_lot)
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


def _unique_paths(paths: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for p in paths:
        s = str(p).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def extra_hide_paths_for_foup_count(foup_count: int) -> List[str]:
    """사용 FOUP 개수 N → ``_PRIMS_1..N`` 누적 숨김.

    - 1개: ``_PRIMS_1`` 만 숨김 (``_2``·``_3`` 은 보임)
    - 2개: ``_PRIMS_1``+``_PRIMS_2`` 숨김 (``_3`` 은 보임)
    - 3개: ``_1``+``_2``+``_3`` 전부 숨김

    목록 인덱스 = FOUP 번호(``_PRIMS_1`` = FOUP1 경로).
    """
    n = max(1, min(3, int(foup_count or 1)))
    a, b, c = _read_hide_lists()
    if n <= 1:
        return list(a)
    if n == 2:
        return _unique_paths(list(a) + list(b))
    return _unique_paths(list(a) + list(b) + list(c))


def extra_show_paths_for_foup_count(foup_count: int) -> List[str]:
    """사용 개수 N 일 때 시작 시 강제 표시할 경로 (미사용 FOUP 목록).

    - 1개: ``_PRIMS_2``+``_PRIMS_3``
    - 2개: ``_PRIMS_3``
    - 3개: (없음)
    """
    n = max(1, min(3, int(foup_count or 1)))
    a, b, c = _read_hide_lists()
    _ = a
    if n <= 1:
        return _unique_paths(list(b) + list(c))
    if n == 2:
        return list(c)
    return []


def all_managed_extra_hide_paths() -> List[str]:
    """1/2/3 목록 합집합."""
    a, b, c = _read_hide_lists()
    return _unique_paths(list(a) + list(b) + list(c))


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
    """이미 main/stage 컨텍스트일 때 동기 적용. Returns foup_count.

    시작 시: 미사용 FOUP 목록(``N+1..3``) 강제 표시 → 사용 개수만큼(``1..N``) 숨김.
    """
    si = max(1, int(screen))
    if foup_count is not None:
        n = max(1, min(3, int(foup_count)))
    else:
        n = count_used_foups_from_dwells(dwells)
    hide_paths = extra_hide_paths_for_foup_count(n)
    # 미사용 FOUP 경로 + 전체 관리 경로(이전 숨김 잔여 제거)
    restore_paths = _unique_paths(
        list(extra_show_paths_for_foup_count(n)) + list(all_managed_extra_hide_paths())
    )
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
    show_n = len(extra_show_paths_for_foup_count(n))
    print(
        f"{_PRINT_PREFIX} screen{si} foup_count={n} "
        f"hide={hidden}/{len(hide_paths)} force_show_unused={shown} "
        f"(unused_list_paths={show_n})",
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
    restore_paths = _unique_paths(
        list(extra_show_paths_for_foup_count(n)) + list(all_managed_extra_hide_paths())
    )
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
    "extra_show_paths_for_foup_count",
    "all_managed_extra_hide_paths",
    "apply_foup_usage_extra_hide_on_stage",
    "restore_foup_usage_extra_hide_on_stage",
    "apply_foup_usage_extra_hide_for_playback",
    "restore_foup_usage_extra_hide",
]

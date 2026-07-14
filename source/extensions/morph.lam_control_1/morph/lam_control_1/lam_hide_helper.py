"""LAM 측 hide refcount + delayed unhide — TBS sequence_engine 의 hide 처리와 동일 의미.

【 정책 】
- step 시작 시 `hide_enabled=True` 면 `hide_prims` (콤마 구분) prim 들을 invisible 처리.
  같은 prim 이 여러 step 에서 hide 되면 refcount + 1 누적.
- step 종료 시 같은 prim 들을 refcount - 1. 0 이 되면 다시 visible.
- step 경계에서의 깜빡임을 막기 위해 unhide 는 작은 지연(기본 0.2s) 후 처리.
- `hide_prims` 의 토큰은 절대 경로면 그대로, 그 외엔 "stage 내부에 있는 절대경로만 지원"
  (LAM 단순화 — TBS 의 `_expand_with_descendants` 와이트카드/이름 검색은 미지원).
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

# IMPORTANT — Kit / pxr 모듈은 반드시 모듈 최상단에서 import 한다(deadlock 방지, lam_translate_animation 주석 참고).
import omni.usd as ou  # type: ignore  # noqa: E402
from pxr import UsdGeom  # type: ignore  # noqa: E402


_PRINT_PREFIX = "[LAM/HIDE]"


class LamHideController:
    """1 시퀀스 동안의 hide 상태를 refcount 로 관리."""

    def __init__(self) -> None:
        self._refcount: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._unhide_thread: Optional[threading.Thread] = None
        self._unhide_queue: List[Dict[str, object]] = []
        self._unhide_evt = threading.Event()
        self._stop_flag = threading.Event()

    # ------------------------------------------------------------------ public

    def hide_for_step(self, hide_enabled: bool, hide_prims: str) -> List[str]:
        """step 시작 시 호출. invisible 처리한 path 목록을 반환."""
        if not hide_enabled:
            return []
        paths = self._tokenize_paths(hide_prims)
        if not paths:
            return []
        with self._lock:
            for p in paths:
                self._refcount[p] = self._refcount.get(p, 0) + 1
        for p in paths:
            self._set_visible(p, False)
        return list(paths)

    def schedule_unhide(self, paths: List[str], delay_sec: float = 0.2) -> None:
        """step 종료 후 paths 의 refcount 를 -1, 0 이면 visible 복귀. 지연 처리."""
        if not paths:
            return
        due = time.monotonic() + max(0.0, float(delay_sec))
        with self._lock:
            self._unhide_queue.append({"due": due, "paths": list(paths)})
        self._ensure_unhide_thread()
        self._unhide_evt.set()

    def clear_all(self) -> None:
        """시퀀스 종료 시 모든 hide 강제 복귀(refcount 무시)."""
        with self._lock:
            paths = list(self._refcount.keys())
            self._refcount.clear()
            self._unhide_queue.clear()
        for p in paths:
            self._set_visible(p, True)
        self._stop_flag.set()
        self._unhide_evt.set()

    # ----------------------------------------------------------------- internal

    def _ensure_unhide_thread(self) -> None:
        if self._unhide_thread and self._unhide_thread.is_alive():
            return
        self._stop_flag.clear()
        self._unhide_thread = threading.Thread(
            target=self._run_unhide_loop, name="lam_hide_unhide_loop", daemon=True
        )
        self._unhide_thread.start()

    def _run_unhide_loop(self) -> None:
        while not self._stop_flag.is_set():
            self._unhide_evt.wait(timeout=0.2)
            self._unhide_evt.clear()
            with self._lock:
                queue_snapshot = list(self._unhide_queue)
            now = time.monotonic()
            still: List[Dict[str, object]] = []
            for item in queue_snapshot:
                due = float(item.get("due", 0.0) or 0.0)
                if due > now:
                    still.append(item)
                    continue
                paths = list(item.get("paths") or [])  # type: ignore[arg-type]
                with self._lock:
                    show_paths: List[str] = []
                    for p in paths:
                        cnt = self._refcount.get(p, 0) - 1
                        if cnt <= 0:
                            self._refcount.pop(p, None)
                            show_paths.append(p)
                        else:
                            self._refcount[p] = cnt
                for p in show_paths:
                    self._set_visible(p, True)
            with self._lock:
                self._unhide_queue = still
            if not still:
                # 큐가 비면 thread 종료. schedule_unhide 가 다시 깨운다.
                return

    def _tokenize_paths(self, raw: str) -> List[str]:
        out: List[str] = []
        for tok in str(raw or "").split(","):
            s = tok.strip()
            if not s:
                continue
            if s.startswith("/"):
                out.append(s)
            else:
                print(
                    f"{_PRINT_PREFIX} only absolute /World/... paths supported, got: {s}",
                    flush=True,
                )
        return out

    def _set_visible(self, path: str, visible: bool) -> None:
        try:
            import omni.usd as ou  # type: ignore
            from pxr import UsdGeom  # type: ignore

            ctx = ou.get_context()
            stage = ctx.get_stage() if ctx else None
            if stage is None:
                return
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                return
            img = UsdGeom.Imageable(prim)
            if not img:
                return
            if visible:
                img.MakeVisible()
            else:
                img.MakeInvisible()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} set_visible({path},{visible}) failed: {exc}", flush=True)


__all__ = ["LamHideController"]

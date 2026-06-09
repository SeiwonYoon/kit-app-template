"""LAM viewport 정책 — 결정 A안 + Phase 5 폴백 (USD_Timeline_Spec.md REQ-007).

전체 화면에 viewport 가 1개만 보이도록, 우선 default viewport 의 `usd_context_name`
을 LAM master context (`morph_lam_master`) 로 마운트한다. 일부 Kit 빌드/버전에서는
`viewport.usd_context_name` setter 가 무시되어 화면이 안 바뀌는 경우가 있는데, 그러면
자동으로 **전용 LAM Viewport 창 1개**를 새로 띄우는 폴백을 가동한다.

사용 흐름:
- `show()` — default viewport 마운트 시도 → 검증(read-back) → 실패 시 폴백 viewport 자동 생성.
- `open_dedicated()` — UI 버튼에서 강제로 폴백 viewport 만 띄울 때 사용.
- `unmount()` — default viewport 컨텍스트 복원.
- `destroy()` — 폴백 viewport 정리 + unmount.
"""

from __future__ import annotations

from typing import Optional


_PRINT_PREFIX = "[TBS/VIEW]"
_DEDICATED_TITLE = "LAM Viewport"


class TbsViewport:
    """default viewport 마운트 + 실패 시 전용 LAM Viewport 창 폴백."""

    def __init__(self, usd_context_name: str) -> None:
        self._usd_context_name = usd_context_name
        self._previous_ctx: Optional[str] = None
        self._mounted: bool = False
        self._dedicated_window = None  # ViewportWindow 폴백

    # ------------------------------------------------------------------ status

    def is_default_visible(self) -> bool:
        """기본 viewport 가 LAM 의 prim 을 자동으로 보고 있는가.

        - default context 모드(`usd_context_name == ""`): 항상 True
        - 명시적 mount 모드: setter 가 적용된 경우에만 True
        """
        if not self._usd_context_name:
            return True
        return self._mounted

    # 레거시 호환 — 이름 변경.
    def is_default_mounted(self) -> bool:
        return self.is_default_visible()

    def has_dedicated(self) -> bool:
        return self._dedicated_window is not None

    def status_text(self) -> str:
        parts = []
        if not self._usd_context_name:
            parts.append("ctx=default('') auto-visible")
        else:
            parts.append(f"mounted={self._mounted}")
            parts.append(f"prev='{self._previous_ctx}'")
            parts.append(f"ctx='{self._usd_context_name}'")
        parts.append(f"dedicated={self.has_dedicated()}")
        return ", ".join(parts)

    # ------------------------------------------------------------------ public

    def show(self) -> bool:
        """default viewport 마운트 → 실패 시 전용 viewport 폴백.

        REQ-007 결정 A' 이후: `usd_context_name == ""` 이면 LAM 도 default 컨텍스트를
        사용하므로 default viewport 가 자연스럽게 LAM 의 prim 을 본다.  → mount/폴백
        둘 다 필요 없음.  사용자가 명시적으로 [강제 열기] 를 누르는 경우에만
        전용 viewport 창을 띄운다.

        반환: viewport 가 LAM 을 볼 수 있는 상태인가.
        """
        if not self._usd_context_name:
            # default context 자체가 LAM 임 → 기본 viewport 가 자동으로 본다.
            print(f"{_PRINT_PREFIX} default context mode — no mount/dedicated needed.", flush=True)
            return True

        ok_default = self._mount_default_viewport()
        if ok_default:
            return True
        # 폴백 — 전용 LAM Viewport 창을 한 개 띄움.
        return self.open_dedicated()

    def open_dedicated(self) -> bool:
        """전용 LAM Viewport 창을 강제로 띄운다(이미 있으면 표시만 갱신)."""
        if self._dedicated_window is not None:
            try:
                self._dedicated_window.visible = True  # type: ignore[attr-defined]
                print(f"{_PRINT_PREFIX} dedicated already open, raised.", flush=True)
                return True
            except Exception:
                self._dedicated_window = None

        try:
            from omni.kit.viewport.window import ViewportWindow  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.kit.viewport.window not available: {exc}", flush=True)
            return False

        try:
            vw = ViewportWindow(_DEDICATED_TITLE, usd_context_name=self._usd_context_name)
            self._dedicated_window = vw
            print(
                f"{_PRINT_PREFIX} dedicated viewport opened (ctx='{self._usd_context_name}')",
                flush=True,
            )
            return True
        except Exception as exc:
            # 일부 빌드에서는 키워드 인자 시그니처가 다를 수 있어 한 번 더 시도.
            try:
                from omni.kit.viewport.window import ViewportWindow  # type: ignore

                vw = ViewportWindow(_DEDICATED_TITLE)
                # 사후 setter 시도.
                try:
                    vw.viewport_api.usd_context_name = self._usd_context_name  # type: ignore[attr-defined]
                except Exception:
                    pass
                self._dedicated_window = vw
                print(
                    f"{_PRINT_PREFIX} dedicated viewport opened (post-set ctx)",
                    flush=True,
                )
                return True
            except Exception as exc2:
                print(
                    f"{_PRINT_PREFIX} dedicated viewport creation failed: {exc} / {exc2}",
                    flush=True,
                )
                return False

    def close_dedicated(self) -> None:
        if self._dedicated_window is None:
            return
        try:
            self._dedicated_window.destroy()  # type: ignore[attr-defined]
        except Exception as exc:
            print(f"{_PRINT_PREFIX} dedicated.destroy failed: {exc}", flush=True)
        self._dedicated_window = None

    def unmount(self) -> None:
        """default viewport 컨텍스트를 마운트 이전 값으로 되돌림."""
        if not self._mounted:
            return
        try:
            from omni.kit.viewport.utility import get_active_viewport  # type: ignore

            vp = get_active_viewport()
            if vp is not None:
                try:
                    vp.usd_context_name = self._previous_ctx or ""
                except Exception as exc:
                    print(f"{_PRINT_PREFIX} cannot restore viewport context: {exc}", flush=True)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} unmount failed: {exc}", flush=True)
        finally:
            self._mounted = False

    def destroy(self) -> None:
        try:
            self.close_dedicated()
        except Exception:
            pass
        try:
            self.unmount()
        except Exception:
            pass

    # ----------------------------------------------------------------- private

    def _mount_default_viewport(self) -> bool:
        """default viewport 의 source 를 LAM 으로 바꾸고, read-back 으로 실제 적용 검증."""
        if self._mounted:
            return True
        try:
            from omni.kit.viewport.utility import get_active_viewport  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.kit.viewport.utility not available: {exc}", flush=True)
            return False

        try:
            vp = get_active_viewport()
            if vp is None:
                print(f"{_PRINT_PREFIX} get_active_viewport() returned None", flush=True)
                return False
            try:
                self._previous_ctx = vp.usd_context_name
            except Exception:
                self._previous_ctx = ""
            try:
                vp.usd_context_name = self._usd_context_name
            except Exception as exc:
                print(f"{_PRINT_PREFIX} cannot set viewport.usd_context_name: {exc}", flush=True)
                return False

            # read-back 검증 — 일부 빌드는 setter 가 무시됨.
            try:
                applied = vp.usd_context_name
            except Exception:
                applied = None
            if applied != self._usd_context_name:
                print(
                    f"{_PRINT_PREFIX} setter ignored — applied='{applied}', expected='{self._usd_context_name}'. fallback to dedicated.",
                    flush=True,
                )
                return False
        except Exception as exc:
            print(f"{_PRINT_PREFIX} viewport mount failed: {exc}", flush=True)
            return False

        self._mounted = True
        print(
            f"{_PRINT_PREFIX} default viewport mounted to LAM context "
            f"(prev='{self._previous_ctx}', new='{self._usd_context_name}')",
            flush=True,
        )
        return True


__all__ = ["LamViewport"]

# ---------------------------------------------------------------------
# service.py  (외부 호출용 Section Control Service)
# ---------------------------------------------------------------------
import asyncio

import carb
import carb.events
import omni.kit.app
import omni.usd

from .core import SectionController


class SectionControlService:
    """
    SectionControlService
    - 섹션(omni.kit.window.section) 백엔드를 보장(ensure)하고,
      SectionController를 통해 enabled/axis/flip/offset을 적용한다.
    - 외부(다른 익스텐션/스크립트)에서 본 서비스의 public API를 호출하는 방식으로 사용한다.
    """

    DEBUG_WARMUP_LOG = True
    WARMUP_FRAMES = 5  # 10이면 눈에 띄게 켜져있을 수 있어서 2~5 추천

    def __init__(self):
        self.controller = SectionController()

        self._stage_event_sub = None
        self._post_update_sub = None

        self._apply_retries_left = 0
        self._apply_attempt = 0

        self._ensured_section_backend_once = False

        # warm-up state
        self._warmup_task = None
        self._warmed_once_for_stage_id = None

    # ---------------- lifecycle ----------------
    def startup(self):
        self._subscribe_stage_events()
        self.ensure_section_backend_running(force=True)

    def shutdown(self):
        try:
            if self._post_update_sub:
                self._post_update_sub.unsubscribe()
        except Exception:
            pass
        self._post_update_sub = None

        try:
            if self._stage_event_sub:
                self._stage_event_sub.unsubscribe()
        except Exception:
            pass
        self._stage_event_sub = None

        self.controller = None
        self._warmup_task = None

    # ---------------- helpers ----------------
    def _log(self, msg: str):
        if self.DEBUG_WARMUP_LOG:
            carb.log_warn(f"[section_control] {msg}")

    async def _wait_for_frames(self, n: int):
        app = omni.kit.app.get_app()
        for _ in range(max(0, int(n))):
            await app.next_update_async()

    def _get_stage_id(self):
        st = omni.usd.get_context().get_stage()
        return None if st is None else id(st)

    @staticmethod
    def _extract_ext_ids(exts):
        if isinstance(exts, dict):
            return list(exts.keys())
        if isinstance(exts, (list, tuple)):
            out = []
            for item in exts:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                    out.append(item[0])
            return out
        return []

    # ---------------- ensure backend ----------------
    def ensure_section_backend_running(self, force: bool = False) -> bool:
        """
        omni.kit.window.section(또는 유사 확장)이 로드/활성화되어 있어야
        SectionManager가 안정적으로 동작한다.

        - force=False: 한 번 성공하면 이후 재시도 안 함
        - force=True: 매번 활성화 시도(디버깅/환경차 대응용)
        """
        if self._ensured_section_backend_once and not force:
            return True

        try:
            app = omni.kit.app.get_app()
            em = app.get_extension_manager()
            all_exts = self._extract_ext_ids(em.get_extensions())
        except Exception as ex:
            self._log(f"ensure_backend: failed to access extension manager: {ex}")
            return False

        candidates = []
        for ext_id in all_exts:
            if not isinstance(ext_id, str):
                continue
            if ext_id == "omni.kit.window.section":
                candidates.append(ext_id)
            elif "window.section" in ext_id:
                candidates.append(ext_id)
            elif ext_id.endswith(".section") and "omni.kit" in ext_id:
                candidates.append(ext_id)

        candidates = list(dict.fromkeys(candidates))
        candidates.sort(key=lambda x: (x != "omni.kit.window.section", x))

        ok_any = False
        for ext_id in candidates:
            try:
                if hasattr(em, "set_extension_enabled_immediate"):
                    em.set_extension_enabled_immediate(ext_id, True)
                else:
                    em.set_extension_enabled(ext_id, True)
                ok_any = True
                self._log(f"ensure_backend: enabled {ext_id}")
            except Exception as ex:
                self._log(f"ensure_backend: enable failed {ext_id}: {ex}")

        if ok_any:
            self._ensured_section_backend_once = True
        else:
            self._log("ensure_backend: no section extension enabled (candidates empty or failed)")

        return ok_any

    # ---------------- warm-up (enable ON 때만) ----------------
    def warmup_section_window(self, force: bool = False):
        """
        최초 enable ON 시 '섹션 윈도우'를 잠깐 show/hide 하여
        내부 위젯/prim 준비를 유도(깜빡임 최소화).
        - post_update apply loop 에서는 호출하지 않는다.
        """
        stage_id = self._get_stage_id()

        if stage_id is None:
            self._log("warmup: stage is None (defer warmup)")
            return

        if (not force) and (self._warmed_once_for_stage_id == stage_id):
            return

        if self._warmup_task is not None:
            return

        async def _do():
            try:
                self.ensure_section_backend_running(force=True)

                try:
                    from omni.kit.window.section import get_instance as get_section_instance
                except Exception as ex:
                    self._log(f"warmup: import get_section_instance failed: {ex}")
                    return

                try:
                    inst = get_section_instance()
                except Exception as ex:
                    self._log(f"warmup: get_section_instance() failed: {ex}")
                    return

                self._log("warmup: show_window(True)")
                try:
                    inst.show_window(None, True)
                except Exception as ex:
                    self._log(f"warmup: show_window(True) failed: {ex}")
                    return

                await self._wait_for_frames(self.WARMUP_FRAMES)

                self._log("warmup: show_window(False)")
                try:
                    inst.show_window(None, False)
                except Exception as ex:
                    self._log(f"warmup: show_window(False) failed: {ex}")

                self._warmed_once_for_stage_id = stage_id
                self._log("warmup: done")

            finally:
                self._warmup_task = None

        self._warmup_task = asyncio.ensure_future(_do())

    # ---------------- state apply ----------------
    def _apply_changes(self, enabled: bool, axis: str, flip: bool, offset: float) -> bool:
        """
        controller 상태값만 갱신하고,
        실제 USD stage 반영은 schedule_apply()에서 post_update loop로 수행한다.
        """
        st0 = self.controller.get_state()
        changed = False

        # ✅ enable ON 순간에만 warm-up 1회
        if enabled and not bool(st0.get("enabled")):
            self._log("enable toggled ON -> ensure backend + warmup(once)")
            self.ensure_section_backend_running(force=True)
            self.warmup_section_window(force=True)

        try:
            if bool(enabled) != bool(st0.get("enabled")):
                self.controller.set_enabled(enabled)
                changed = True

            if (axis or "").upper() != (st0.get("axis") or "").upper():
                self.controller.set_axis(axis)
                changed = True

            if bool(flip) != bool(st0.get("flip")):
                self.controller.set_flip(flip)
                changed = True

            if abs(float(offset) - float(st0.get("offset", 0.0))) > 1e-9:
                self.controller.set_offset(offset)
                changed = True

        except Exception as ex:
            self._log(f"_apply_changes exception: {ex}")
            changed = True

        return changed

    # ---------------- Public API (외부 호출용) ----------------
    def get_state(self) -> dict:
        """현재 service/controller 상태 조회 (즉시)"""
        return self.controller.get_state()

    def set_all(self, enabled: bool, axis: str, flip: bool, offset: float, reason: str = "set_all") -> dict:
        """enabled/axis/flip/offset 한번에 설정 + (필요 시) schedule_apply"""
        changed = self._apply_changes(enabled, axis, flip, offset)
        if changed:
            self.schedule_apply(reason)
        return self.controller.get_state()

    def set_enabled(self, enabled: bool, reason: str = "set_enabled") -> dict:
        st0 = self.controller.get_state()
        return self.set_all(
            enabled=bool(enabled),
            axis=str(st0.get("axis", "X")),
            flip=bool(st0.get("flip", False)),
            offset=float(st0.get("offset", 0.0)),
            reason=reason,
        )

    def set_axis(self, axis: str, reason: str = "set_axis") -> dict:
        st0 = self.controller.get_state()
        return self.set_all(
            enabled=bool(st0.get("enabled", False)),
            axis=str(axis),
            flip=bool(st0.get("flip", False)),
            offset=float(st0.get("offset", 0.0)),
            reason=reason,
        )

    def set_flip(self, flip: bool, reason: str = "set_flip") -> dict:
        st0 = self.controller.get_state()
        return self.set_all(
            enabled=bool(st0.get("enabled", False)),
            axis=str(st0.get("axis", "X")),
            flip=bool(flip),
            offset=float(st0.get("offset", 0.0)),
            reason=reason,
        )

    def set_offset(self, offset: float, reason: str = "set_offset") -> dict:
        st0 = self.controller.get_state()
        try:
            off = float(offset)
        except Exception:
            off = 0.0
        return self.set_all(
            enabled=bool(st0.get("enabled", False)),
            axis=str(st0.get("axis", "X")),
            flip=bool(st0.get("flip", False)),
            offset=off,
            reason=reason,
        )

    def apply_now(self, reason: str = "apply_now", retries: int = 240) -> None:
        """
        변경된 dirty 값을 반영하도록 apply loop를 예약.
        - set_* 호출은 내부적으로 schedule_apply를 수행하므로 일반적으로 호출할 필요는 없다.
        """
        self.schedule_apply(reason, retries=retries)

    # ---------------- stage events ----------------
    def _subscribe_stage_events(self):
        try:
            ctx = omni.usd.get_context()
            self._stage_event_sub = ctx.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="section_control_stage_events",
            )
        except Exception:
            pass

    def _on_stage_event(self, e: carb.events.IEvent):
        # stage 교체 시 다음 enable ON 때 warm-up 다시 하도록 초기화
        self._warmed_once_for_stage_id = None

        if self.controller and self.controller.get_state().get("enabled"):
            # stage 교체 후에도 section이 켜져 있으면 apply만 재시도
            self.schedule_apply("stage_event_enabled")

    # ---------------- apply loop ----------------
    def schedule_apply(self, reason: str, retries: int = 240):
        """
        post_update에서 apply_once_if_possible 를 재시도.
        - warm-up은 여기서 호출하지 않는다(깜빡임 최소화 목적).
        """
        self._apply_retries_left = max(self._apply_retries_left, retries)

        if self._post_update_sub is None:
            stream = omni.kit.app.get_app().get_post_update_event_stream()
            self._post_update_sub = stream.create_subscription_to_pop(
                self._on_post_update,
                name="section_control_post_update_apply_loop",
            )

    def _on_post_update(self, e):
        if self._apply_retries_left <= 0:
            if self._post_update_sub:
                self._post_update_sub.unsubscribe()
                self._post_update_sub = None
            return

        self._apply_retries_left -= 1
        self._apply_attempt += 1

        try:
            ok = self.controller.apply_once_if_possible(self._apply_attempt)
            if ok:
                self._log("apply_once_if_possible: OK")
                self._apply_retries_left = 0
            else:
                if self._apply_attempt % 30 == 0:
                    self._log("apply_once_if_possible: still not ready")
        except Exception as ex:
            if self._apply_attempt % 30 == 0:
                self._log(f"apply exception: {ex}")

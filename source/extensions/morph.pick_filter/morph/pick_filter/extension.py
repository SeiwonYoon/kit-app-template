# morph/pick_filter/extension.py
# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import omni.ext
import omni.ui as ui
import carb

from .service import ensure_service, get_service
from .ui_dummy import PickFilterDummyUI

WINDOW_TITLE = "Pick Filter"


class MyExtension(omni.ext.IExt):
    """
    Pick Filter Extension
    - on_startup: service ensure + dummy UI window show
    - on_shutdown: dummy UI shutdown + service stop
    """

    def on_startup(self, ext_id):
        self._svc = None
        self._ui = None
        self._window = None

        # ✅ 서비스 확보/시작
        self._svc = ensure_service()

        # ✅ 더미 UI 윈도우 생성
        self._window = ui.Window(WINDOW_TITLE, width=880, height=860)
        self._window.visible = True

        with self._window.frame:
            self._ui = PickFilterDummyUI(self._svc)
            self._ui.build()

        carb.log_info("[pick_filter] started (service ensured + dummy UI shown)")

    def on_shutdown(self):
        # ✅ 더미 UI 정리(태스크 cancel, 모델 구독 해제 등)
        try:
            if self._ui:
                self._ui.shutdown()
        except Exception:
            pass
        self._ui = None

        # ✅ 윈도우 정리
        try:
            self._window = None
        except Exception:
            pass

        # ✅ 서비스 정리
        try:
            svc = get_service()
            if svc:
                svc.stop()
        except Exception:
            pass

        carb.log_info("[pick_filter] shutdown complete")
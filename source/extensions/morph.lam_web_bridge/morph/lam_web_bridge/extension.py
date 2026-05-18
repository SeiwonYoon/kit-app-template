# SPDX-FileCopyrightText: Copyright (c) 2026 Morph. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""LAM Web Bridge — HTTP 원격 UI (``morph.lam_control`` 세션에 연결)."""

from __future__ import annotations

import os

import omni.ext

from morph.lam_control.remote_api import get_session

from .lam_remote_http_bridge import start_lam_remote_http_bridge, stop_lam_remote_http_bridge

_PRINT_PREFIX = "[LAM Web Bridge]"


def _want_remote_http_bridge() -> bool:
    v = os.environ.get("TBS_REMOTE_UI", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


class LamWebBridgeExtension(omni.ext.IExt):
    def on_startup(self, ext_id: str) -> None:
        print(f"{_PRINT_PREFIX} on_startup ext_id={ext_id}", flush=True)
        if not _want_remote_http_bridge():
            print(f"{_PRINT_PREFIX} disabled (TBS_REMOTE_UI=0)", flush=True)
            return
        if get_session() is None:
            print(
                f"{_PRINT_PREFIX} morph.lam_control session not ready — bridge not started",
                flush=True,
            )
            return
        try:
            start_lam_remote_http_bridge()
        except Exception as exc:
            print(f"{_PRINT_PREFIX} start failed: {exc}", flush=True)

    def on_shutdown(self) -> None:
        print(f"{_PRINT_PREFIX} on_shutdown", flush=True)
        try:
            stop_lam_remote_http_bridge()
        except Exception:
            pass

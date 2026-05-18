# SPDX-FileCopyrightText: Copyright (c) 2026 Morph. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""LAM Kit 세션 — ``morph.lam_web_bridge`` 등 외부 확장이 코어에 접근할 때 사용.

``LamControlExtension`` 이 startup/shutdown 시 등록·해제한다.
웹 브리지는 ``get_session()`` 으로 registry/scheduler/Open Master 를 호출한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

OpenMasterFn = Callable[..., bool]


@dataclass
class LamKitSession:
    """LAM Control 이 노출하는 최소 Kit API (웹·원격 연동용)."""

    registry: Any
    scheduler: Any
    open_master_at_path: OpenMasterFn

    def instance_count(self) -> int:
        try:
            return len(self.registry.all_instances())
        except Exception:
            return 0


_session: Optional[LamKitSession] = None


def set_session(session: LamKitSession) -> None:
    global _session
    _session = session


def get_session() -> Optional[LamKitSession]:
    return _session


def clear_session() -> None:
    global _session
    _session = None


__all__ = [
    "LamKitSession",
    "set_session",
    "get_session",
    "clear_session",
]

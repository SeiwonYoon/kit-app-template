# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

import carb
import carb.events
import omni.kit.app
import my_company.usd_loader
import asyncio

from carb.eventdispatcher import get_eventdispatcher

class ExtensionManager:
    """USD 로딩 메시지를 처리하는 클래스"""

    def __init__(self):
        self._subscriptions = []

        # loadUSD 이벤트 등록
        event_type = 'loadUSD'
        omni.kit.app.register_event_alias(
            carb.events.type_from_string(event_type),
            event_type,
        )

        ed = get_eventdispatcher()
        self._subscriptions.append(
            ed.observe_event(
                observer_name=f"ExtensionManager:{event_type}",
                event_name=event_type,
                on_event=self._on_load_usd,
            )
        )

    def _on_load_usd(self, event: carb.events.IEvent) -> None:
        """USD 파일을 로드하는 핸들러"""
        if "path" not in event.payload:
            error_msg = "Missing 'path' in payload"
            carb.log_error(error_msg)
            return

        path = event.payload["path"]
        print(f"path: {path}")

        usdLoader = my_company.usd_loader.get_instance()
        print(f"usdLoader: {usdLoader._validate_and_load_path}")
        asyncio.ensure_future(usdLoader._validate_and_load_path(path))

    def on_shutdown(self) -> None:
        """Extension이 비활성화될 때 호출되어 상태를 정리합니다."""
        self._subscriptions.clear()
        carb.log_info("ExtensionManager shutdown complete")

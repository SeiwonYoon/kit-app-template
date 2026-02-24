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
import my_company.usd_loader
import asyncio
from typing import Dict, Callable, List

from .base_handler import BaseHandler


class UsdLoaderHandler(BaseHandler):
    """usd_loader 익스텐션과의 메시지 통신을 처리하는 클래스"""

    def __init__(self):
        self._usd_loader = my_company.usd_loader.get_instance()
        print(f"self._usd_loader: {self._usd_loader}")
        super().__init__()

    def get_outgoing_events(self) -> List[str]:
        """클라이언트로 보낼 이벤트 리스트"""
        return [
            "usdLoadComplete",
            "usdLoadError",
        ]

    def get_event_handlers(self) -> Dict[str, Callable]:
        """이벤트 핸들러 맵 반환"""
        return {
            'loadUSD': self._on_load_usd,
        }

    def _on_load_usd(self, event: carb.events.IEvent) -> None:
        """USD 파일을 로드하는 핸들러"""
        if "path" not in event.payload:
            carb.log_error("Missing 'path' in payload")
            self.dispatch_event("usdLoadError", {"error": "Missing 'path' in payload"})
            return

        if not self._usd_loader:
            self._usd_loader = my_company.usd_loader.get_instance()

        path = event.payload["path"]
        carb.log_info(f"UsdLoaderHandler received path: {path}")
        asyncio.ensure_future(self._usd_loader._validate_and_load_path(path))

        print(f"self._usd_loader: {self._usd_loader}")
        # 로딩 완료 시 이벤트 전송 예제 (실제로는 async 완료 후 전송해야 함)
        self.dispatch_event("usdLoadComplete", {"path": path})

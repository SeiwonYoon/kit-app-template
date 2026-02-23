# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

from .base_handler import BaseHandler
from .usdLoader_handler import UsdLoaderHandler

# 여기에 새 핸들러를 추가하세요
HANDLERS = [
    UsdLoaderHandler,
]

__all__ = ["BaseHandler", "UsdLoaderHandler", "HANDLERS"]

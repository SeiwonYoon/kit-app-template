# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import carb
import carb.settings
import omni.ext

_TARGET = "ko-KR"

# Preferences에서 보통 참조되는 값
PERSIST_LOCALE = "/persistent/app/locale_id"

# user.config.json에 "persistent -> 0 -> app -> locale_id" 같은 가지가 함께 생기는 케이스가 있어
# 안전하게 같이 맞춰준다.
PERSIST_0_LOCALE = "/persistent/0/app/locale_id"

# 런타임 즉시 적용(필요하면 같이)
RUNTIME_LOCALE = "/app/locale_id"


class ForceKoLocaleExtension(omni.ext.IExt):
    def on_startup(self, ext_id: str):
        settings = carb.settings.get_settings()

        # warmup 모드는 건드리지 않는 편이 안전
        try:
            if settings.get("/app/warmupMode"):
                return
        except Exception:
            pass

        before_persist = settings.get_as_string(PERSIST_LOCALE) or ""
        before_persist0 = settings.get_as_string(PERSIST_0_LOCALE) or ""
        before_runtime = settings.get_as_string(RUNTIME_LOCALE) or ""

        # 핵심: persistent/app/locale_id 강제
        settings.set(PERSIST_LOCALE, _TARGET)

        # 보조: persistent/0/app/locale_id도 같이 맞춤
        settings.set(PERSIST_0_LOCALE, _TARGET)

        # 선택: 런타임도 즉시 맞춤(원치 않으면 이 줄만 빼면 됨)
        settings.set(RUNTIME_LOCALE, _TARGET)

        after_persist = settings.get_as_string(PERSIST_LOCALE) or ""
        after_persist0 = settings.get_as_string(PERSIST_0_LOCALE) or ""
        after_runtime = settings.get_as_string(RUNTIME_LOCALE) or ""

        carb.log_warn(
            "[ForceKoLocale] "
            f"{PERSIST_LOCALE}: '{before_persist}' -> '{after_persist}' | "
            f"{PERSIST_0_LOCALE}: '{before_persist0}' -> '{after_persist0}' | "
            f"{RUNTIME_LOCALE}: '{before_runtime}' -> '{after_runtime}'"
        )

    def on_shutdown(self):
        pass

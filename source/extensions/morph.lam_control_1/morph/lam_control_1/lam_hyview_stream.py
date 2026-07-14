"""HyView / streaming layout 훅 — LAM v1 은 기본 no-op (TBS 구조만 유지)."""

from __future__ import annotations

from typing import Any


def is_hyview_stream_layout_locked() -> bool:
    return False


def bridge_stream_skip() -> bool:
    return False


def apply_streaming_livestream_settings() -> None:
    pass


def install_streaming_window_resize_hooks(ext: Any) -> None:
    _ = ext


def teardown_streaming_window_hooks(ext: Any) -> None:
    _ = ext


def enable_hyview_stream_layout_lock(ext: Any) -> None:
    _ = ext


def is_streaming_deployment() -> bool:
    return False


__all__ = [
    "apply_streaming_livestream_settings",
    "bridge_stream_skip",
    "enable_hyview_stream_layout_lock",
    "install_streaming_window_resize_hooks",
    "is_hyview_stream_layout_locked",
    "is_streaming_deployment",
    "teardown_streaming_window_hooks",
]

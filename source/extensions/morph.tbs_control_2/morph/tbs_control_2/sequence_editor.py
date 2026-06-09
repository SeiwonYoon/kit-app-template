# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""TBS 시퀀스 편집기 — LAM 편집기(`TbsLamSequenceEditor`) 래퍼."""

from __future__ import annotations

from typing import Any, Optional

from .tbs_data_paths import resolve_local_data_path
from .tbs_lam_sequence_editor import STEP_TYPES, TbsLamSequenceEditor, _coerce_loaded_step
from .sequence_engine import SequenceRunner, capture_composed_local_start_snapshot_for_paths, resolve_prim_paths_multi

__all__ = [
    "SequenceEditorWindow",
    "SequenceRunner",
    "STEP_TYPES",
    "_coerce_loaded_step",
    "capture_composed_local_start_snapshot_for_paths",
    "resolve_prim_paths_multi",
]


class SequenceEditorWindow:
    """extension 에서 registry/scheduler/evaluator 주입 후 사용."""

    def __init__(
        self,
        registry: Any,
        scheduler: Any,
        *,
        evaluator: Any = None,
        title: str = "TBS Sequence Editor",
    ) -> None:
        seq_dir = resolve_local_data_path("sim_sequences") or ""
        self._editor = TbsLamSequenceEditor(
            registry,
            scheduler,
            default_dir=seq_dir,
            evaluator=evaluator,
        )
        self._title = title

    def show(self) -> None:
        self._editor.show()

    def destroy(self) -> None:
        try:
            self._editor.destroy()
        except Exception:
            pass

"""LAM Independent Playback — Option E 의 인스턴스 단위 runtime (Phase A 골격).

본 모듈은 `docs/LAM_Independent_Playback_Plan.md` §5 Phase A 의 신규 추가 모듈이다.

## 책임 (1 instance = 1 runtime 객체)

1. **Offscreen Stage 격리 평가**
   - 자산 USD 를 in-memory 로 `Usd.Stage.Open` 하여 독립 stage 1 개를 보유.
   - 매 frame `SetCurrentTimeCode(virtual_time * tps)` 로 자기 inst 의 timeCode 만 평가.
   - 다른 instance / master stage 의 timeline 과 완전 분리 (USD value resolution 의
     stage-global current_time 한계를 우회).

2. **Master Mirror 에 default value write**
   - master stage 의 동일 prim_path 산하 attribute 에 평가값을 **default value** 로 author.
   - master stage 자체는 timeCode 진행 안 함 (정적). 따라서 reference 안의 timeSamples
     보다 root layer 의 default opinion 이 winner → 시각적으로 사용자가 보는 viewport
     는 instance 마다 다른 시각의 결과가 동시에 표시.

3. **Dispose**
   - 자기 stage 1 개를 GC 가능 상태로 풀어주고 master mirror prim 의 author 흔적도
     정리(상위 호출자가 prim 자체를 제거하는 별도 흐름은 본 모듈 관여 X).

## Phase B+ wiring

`lam_runtime_evaluator.RuntimeEvaluator` 가 `_RUNTIME_USE_OPTION_E=True` 일 때
`AnimationInstanceRuntime` 를 instance 마다 1 개씩 lazy 생성한다. `morph.tbs_control_1`
import 0.

## multi-instance / multi-JSON 동시 재생과의 정합성

사용자 요구 (2026-05-11 합의):
- 1 step 에 multi-instance timeline / multi-prim MOVE·ROTATE 동시 set 가능.
- 여러 JSON 이 wall clock 시간축 위에서 중첩 실행 (event_1@1s 5s 길이 +
  event_2@3s 중첩). 두 JSON 은 서로 독립 player 가 진행.

본 runtime 의 API 는 1 instance 단위로 self-contained 이므로, 외부 scheduler / player
가 instance 마다 1 개씩 runtime 객체를 보유하기만 하면 자연 동작한다:
- 어느 player 가 자기 target instance 의 `virtual_time` 을 set 하든, evaluator 가 매
  frame 모든 runtime 의 `evaluate_and_write()` 를 호출하므로 last-writer-winner.
- 동일 instance 가 두 player 의 target 이 되면 마지막 player 의 vt 가 winner — 사용자
  보장 영역(같은 stage USD timeline 을 두 JSON 에 동시 포함하지 않는다고 합의됨).

## TBS 영향

본 모듈은 **`morph.tbs_control_1` 의 어떤 심볼도 import 하지 않는다**.
TBS 의 default context / stage / sequence runner / port_lot_visibility 등 보호 영역
변경 0 (LAM_Independent_Playback_Plan.md §3.1).
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .lam_types import AnimationInstance, LAM_FIXED_FPS

# Phase A 단계에서는 USD / omni 가 import 되지 않은 환경에서도 본 모듈 import 자체가
# 깨지면 안 된다 (lam_master_stage / lam_attribute_reauthor 와 동일 패턴).
# 실제 stage 조작은 lazy import 후 가용 시에만 수행한다.
try:  # pragma: no cover - import 가용성에 따라 분기
    from pxr import Sdf as _Sdf  # type: ignore
    from pxr import Usd as _Usd  # type: ignore
except Exception:  # pragma: no cover
    _Sdf = None  # type: ignore
    _Usd = None  # type: ignore


_PRINT_PREFIX = "[LAM/Runtime]"


def _normalize_asset_path_for_cache(path: str) -> str:
    """Windows 경로 대소문자/슬래시 차이로 동일 파일을 중복 open 시도하지 않도록 정규화."""
    try:
        return os.path.normcase(os.path.normpath(os.path.expanduser(str(path))))
    except Exception:
        return str(path)


class _AttrSampleEntry:
    """Offscreen stage 의 1 attribute 와 그 timeSamples 범위 메타.

    매 frame attribute traverse 비용을 줄이기 위해 첫 evaluate 시 1 회 캐싱한다.
    `attr` 는 offscreen stage 의 `Usd.Attribute`, `mirror_attr` 는 master stage 의
    동일 path attribute (write 대상). 둘 다 캐시 후 매 frame Get/Set 만 호출한다.
    """

    __slots__ = ("attr", "mirror_attr", "min_tc", "max_tc")

    def __init__(self, attr: Any, mirror_attr: Any, min_tc: float, max_tc: float) -> None:
        self.attr = attr
        self.mirror_attr = mirror_attr
        self.min_tc = float(min_tc)
        self.max_tc = float(max_tc)


class AnimationInstanceRuntime:
    """`AnimationInstance` 1 개의 offscreen 평가 + master mirror write 를 담당하는 runtime.

    수명 주기:
        ```
        rt = AnimationInstanceRuntime(instance, master_stage)
        rt.setup_offscreen_stage(asset_path)
        rt.setup_master_mirror_prim()       # master prim 보장
        # 매 frame:
        rt.evaluate_and_write(virtual_time)
        # 종료:
        rt.dispose()
        ```

    1 인스턴스 = 1 runtime 객체. 같은 prim_path 의 인스턴스가 두 번 등록되어도
    runtime 은 1 개만 (그게 last-writer-winner 의 단일 entry point).

    본 클래스는 thread-safe 하지 않다 — `evaluate_and_write` 는 evaluator 의
    update tick (main thread) 에서만 호출되도록 한다.
    """

    # ------------------------------------------------------------------ init / dispose

    def __init__(
        self,
        instance: AnimationInstance,
        master_stage: Optional[Any] = None,
    ) -> None:
        self._instance: AnimationInstance = instance
        self._master_stage: Optional[Any] = master_stage  # pxr.Usd.Stage (master)
        self._offscreen_stage: Optional[Any] = None       # pxr.Usd.Stage (in-memory)
        self._offscreen_asset_path: str = ""
        # `Usd.Stage.Open` 이 한 번이라도 실패한 자산 경로 — Option E 가 매 프레임
        # `setup_offscreen_stage` 를 다시 호출해도 동일 경로에 대해 Open 을 재시도하지 않는다
        # (미존재/손상 파일 시 무한 로그·프리즈 방지). 경로가 바뀌면 자동으로 해제된다.
        self._offscreen_open_failed_key: str = ""

        # `lam_runtime_evaluator` 가 open 실패 시 `[LAM/Runtime] OPTION_E ...` 를 1 회만 찍기 위함.
        self._lam_option_e_setup_fail_logged: bool = False

        # offscreen 에서 sample 이 있는 attribute → master mirror attribute 매핑 캐시.
        self._attr_cache: List[_AttrSampleEntry] = []
        self._attr_cache_built: bool = False

        # 진단 — 인스턴스별 첫 evaluate 시 1 회 print 용.
        self._diag_first_eval_logged: bool = False
        self._diag_first_eval_warned_zero: bool = False

        # 직전 평가에서 실제 write 된 attribute 개수 (외부 진단 / 테스트용).
        self._last_wrote: int = 0
        self._last_virtual_time: float = float("nan")

        # offscreen default/root prim path (자산마다 `/Root` 등). Skel 경로 매핑에 사용.
        self._offscreen_root_path_str: str = ""
        # UsdSkel.Cache 는 인스턴스당 1 개 재사용 (Populate 만 매 프레임 아님).
        self._skel_cache: Any = None
        self._skel_write_diag_logged: bool = False

    @property
    def instance(self) -> AnimationInstance:
        return self._instance

    @property
    def prim_path(self) -> str:
        return self._instance.prim_path

    @property
    def offscreen_asset_path(self) -> str:
        return self._offscreen_asset_path

    def clear_offscreen_open_failure(self) -> None:
        """이전 ``Usd.Stage.Open`` 실패 캐시를 지운다 — 파일을 복구한 뒤 같은 경로로 재시도할 때."""
        self._offscreen_open_failed_key = ""
        self._lam_option_e_setup_fail_logged = False

    @property
    def is_ready(self) -> bool:
        """평가가 의미를 가지려면 offscreen stage + master stage 둘 다 필요."""
        return self._offscreen_stage is not None and self._master_stage is not None

    @property
    def last_wrote(self) -> int:
        return self._last_wrote

    @property
    def last_virtual_time(self) -> float:
        return self._last_virtual_time

    def set_master_stage(self, master_stage: Optional[Any]) -> None:
        """LAM Window / Evaluator 가 master stage 가 교체될 때 호출.

        master stage 교체 시 mirror attribute 캐시는 무효화(다음 evaluate 에서 재빌드).
        """
        if master_stage is self._master_stage:
            return
        self._master_stage = master_stage
        self._invalidate_attr_cache()

    def sync_mirror_root_prim_path_from_master(
        self, asset_path_hint: str = ""
    ) -> None:
        """master 합성 + 자산 경로 기준으로 ``AnimationInstance.mirror_root_prim_path`` 설정.

        Kit drag&drop 으로 자산이 ``/World/inst/test1/N_07...`` 처럼 인스턴스 직속이 아닌
        경로에 박힌 경우에만 비어 있지 않은 경로가 설정된다. Option E 의 첫
        ``_build_attr_cache`` 가 offscreen ``/Root`` 를 그 경로 아래에만 매핑하도록,
        offscreen stage 를 열기 **직전**에 호출하는 것이 안전하다.

        Args:
            asset_path_hint: 호출자가 알고 있는 자산 절대 경로 (또는 ``file:/`` URI).
                비어 있으면 ``inst.source_asset`` → master ``_discover_asset_path_from_master``
                순서로 fallback. Bake / Extract 같이 호출 측이 이미 자산 경로를 알고 있을
                때 명시 전달하면 ``inst.source_asset`` 미설정/지연 갱신 케이스에서도 mirror
                매핑이 정확히 잡힌다.
        """
        if self._master_stage is None:
            return
        try:
            from .lam_extract_from_master import (
                _discover_asset_path_from_master,
                discover_drag_drop_asset_root_prim,
                normalize_asset_uri_to_path,
            )

            inst = self._instance
            raw = normalize_asset_uri_to_path(
                (str(asset_path_hint or "") or "").strip()
            )
            if not raw:
                raw = normalize_asset_uri_to_path(
                    (getattr(inst, "source_asset", "") or "").strip()
                )
            if not raw:
                try:
                    raw = normalize_asset_uri_to_path(
                        _discover_asset_path_from_master(
                            self._master_stage, self.prim_path
                        )
                    )
                except Exception:
                    raw = ""
            mr = ""
            if raw:
                try:
                    mr = (
                        discover_drag_drop_asset_root_prim(
                            self._master_stage, self.prim_path, raw
                        )
                        or ""
                    )
                except Exception:
                    mr = ""
            inst.mirror_root_prim_path = mr
            if mr:
                print(
                    f"{_PRINT_PREFIX} mirror_root_prim_path={mr} prim={self.prim_path} "
                    f"asset={raw or '(unknown)'}",
                    flush=True,
                )
            else:
                print(
                    f"{_PRINT_PREFIX} mirror_root_prim_path=(empty) prim={self.prim_path} "
                    f"asset={raw or '(unknown)'} — inst 직속에 mirror author",
                    flush=True,
                )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} sync_mirror_root_prim_path_from_master FAIL "
                f"prim={self.prim_path}: {exc}",
                flush=True,
            )

    # ------------------------------------------------------------------ setup

    def setup_offscreen_stage_from_layer(
        self,
        baked_layer: Any,
        *,
        asset_path: str = "",
        mirror_asset_path_hint: str = "",
    ) -> bool:
        """**X3 in-memory bake 경로** — anonymous `Sdf.Layer` 를 root 로 offscreen Stage 를 연다.

        ``lam_bake_omnigraph.bake_prim_to_timesamples_async(output_mode='memory')`` 가
        반환한 anonymous Sdf.Layer 를 그대로 root layer 로 사용해 Stage 를 open. 이렇게
        하면 디스크에 ``*_baked.usd`` 를 만들지 않고도 baked timeSamples 가 들어간
        offscreen Stage 로 SetCurrentTimeCode 평가가 가능하다 (휘발성 — Kit 종료 시
        layer 가 메모리에서 소멸).

        Args:
            baked_layer: ``pxr.Sdf.Layer``. anonymous 가 일반적이지만 file-backed 도 허용.
            asset_path: 원본 자산 경로. 로그 / `_offscreen_asset_path` 식별용. 평가에는
                직접 영향 없음 (root layer 는 `baked_layer`).

        Returns:
            성공 여부.
        """
        if baked_layer is None:
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage_from_layer SKIP baked_layer is None "
                f"prim={self.prim_path}",
                flush=True,
            )
            return False
        if _Usd is None:
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage_from_layer SKIP pxr.Usd unavailable "
                f"prim={self.prim_path}",
                flush=True,
            )
            return False

        # mirror_asset_path_hint 가 있으면 우선 — Extract / Bake 가 baked layer 만 갖고
        # 호출하는 케이스에서 인스턴스 ``source_asset`` 이 아직 비어 있어도 drag&drop
        # 자산 루트를 정확히 인식하도록 한다. 없으면 기존 fallback (asset_path →
        # inst.source_asset → master 합성 스캔) 사용.
        self.sync_mirror_root_prim_path_from_master(
            mirror_asset_path_hint or asset_path
        )

        # 기존 offscreen stage 가 있으면 먼저 비움 — 같은 layer 라도 reload 일 수 있음.
        if self._offscreen_stage is not None:
            self._release_offscreen_stage()

        try:
            stage = _Usd.Stage.Open(baked_layer)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage_from_layer FAIL prim={self.prim_path} "
                f"asset={asset_path} exc={exc}",
                flush=True,
            )
            return False
        if stage is None:
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage_from_layer returned None "
                f"prim={self.prim_path} asset={asset_path}",
                flush=True,
            )
            return False

        self._offscreen_stage = stage
        # 식별자 — 외부 진단 출력에서 baked layer 라는 점을 분명히 한다.
        try:
            ident = baked_layer.identifier
        except Exception:
            ident = "<unknown>"
        self._offscreen_asset_path = (
            asset_path or f"memory-baked:{ident}"
        )
        self._offscreen_open_failed_key = ""
        self._lam_option_e_setup_fail_logged = False
        self._invalidate_attr_cache()

        # FPS 30 정규화 — bake 단계에서 이미 30 으로 박지만 안전 차원에서 한 번 더.
        try:
            cur_tcps = float(stage.GetTimeCodesPerSecond())
        except Exception:
            cur_tcps = -1.0
        if abs(cur_tcps - LAM_FIXED_FPS) > 1e-6:
            try:
                stage.SetTimeCodesPerSecond(LAM_FIXED_FPS)
                stage.SetFramesPerSecond(LAM_FIXED_FPS)
            except Exception:
                pass

        try:
            start_tc = float(stage.GetStartTimeCode())
            end_tc = float(stage.GetEndTimeCode())
        except Exception:
            start_tc, end_tc = 0.0, 0.0
        print(
            f"{_PRINT_PREFIX} setup_offscreen_stage_from_layer OK prim={self.prim_path} "
            f"layer={ident} timeCode=[{start_tc},{end_tc}]@{LAM_FIXED_FPS}fps "
            f"src_asset={asset_path or '(unset)'}",
            flush=True,
        )
        return True

    def setup_offscreen_stage(self, asset_path: str) -> bool:
        """자산 USD 를 in-memory offscreen Stage 로 open.

        반환: True 면 정상, False 면 USD 미가용 / 파일 미존재 / open 실패 등.
        - 본 호출은 master stage 를 일절 건드리지 않는다.
        - 이미 다른 offscreen stage 가 열려 있으면 먼저 dispose 후 재로드.
        - **In-memory bake (X3)** 사용 시에는 본 메서드 대신
          `setup_offscreen_stage_from_layer` 를 호출한다.
        """
        if not asset_path:
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage SKIP empty asset_path "
                f"prim={self.prim_path}",
                flush=True,
            )
            return False

        if _Usd is None:
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage SKIP pxr.Usd unavailable "
                f"prim={self.prim_path} asset={asset_path}",
                flush=True,
            )
            return False

        key = _normalize_asset_path_for_cache(asset_path)
        if self._offscreen_open_failed_key:
            if self._offscreen_open_failed_key == key:
                # Option E 가 매 프레임 `not offscreen_asset_path` 분기로 재호출하는 경우
                # 여기서 즉시 반환 — `Usd.Stage.Open` 무한 재시도·로그 폭주 방지.
                return False
            self._offscreen_open_failed_key = ""

        self.sync_mirror_root_prim_path_from_master(asset_path)

        # 이미 같은 자산이 열려 있으면 재사용.
        if self._offscreen_stage is not None and self._offscreen_asset_path == asset_path:
            return True

        # 다른 자산이 열려 있으면 먼저 비움.
        if self._offscreen_stage is not None:
            self._release_offscreen_stage()

        try:
            stage = _Usd.Stage.Open(asset_path)
        except Exception as exc:
            self._offscreen_open_failed_key = key
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage FAIL prim={self.prim_path} "
                f"asset={asset_path} exc={exc}",
                flush=True,
            )
            return False

        if stage is None:
            self._offscreen_open_failed_key = key
            print(
                f"{_PRINT_PREFIX} setup_offscreen_stage returned None prim={self.prim_path} "
                f"asset={asset_path}",
                flush=True,
            )
            return False

        self._offscreen_open_failed_key = ""
        self._lam_option_e_setup_fail_logged = False
        self._offscreen_stage = stage
        self._offscreen_asset_path = asset_path
        self._invalidate_attr_cache()

        # Payload 미로드 시 합성 단계에서 mesh/skel 이 비어 timeSamples 가 0 으로 보이는
        # 케이스 방지 — FBX→USD 자산에서 흔함.
        n_pay = self._load_all_payloads(stage)
        if n_pay > 0:
            print(
                f"{_PRINT_PREFIX} payloads loaded prim={self.prim_path} count={n_pay} "
                f"asset={asset_path}",
                flush=True,
            )

        # FPS 30 정책 — offscreen stage 의 메타 tps 도 30 으로 정규화. timeSamples 자체는
        # 그대로 두고 stage 의 시각 해석 기준만 30 으로 맞춰 evaluator/runtime 의 vt 환산과
        # 정합되도록 한다. 자산 파일은 일절 수정하지 않음 (in-memory stage 의 메타만 변경).
        try:
            cur_tcps = float(stage.GetTimeCodesPerSecond())
        except Exception:
            cur_tcps = -1.0
        if abs(cur_tcps - LAM_FIXED_FPS) > 1e-6:
            try:
                stage.SetTimeCodesPerSecond(LAM_FIXED_FPS)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} offscreen SetTimeCodesPerSecond({LAM_FIXED_FPS}) FAIL "
                    f"prim={self.prim_path} (asset tps={cur_tcps}): {exc}",
                    flush=True,
                )
            try:
                stage.SetFramesPerSecond(LAM_FIXED_FPS)
            except Exception:
                pass

        # 진단 — stage 정보 1 회 출력.
        try:
            tcps = float(stage.GetTimeCodesPerSecond())
        except Exception:
            tcps = -1.0
        try:
            start_tc = float(stage.GetStartTimeCode())
            end_tc = float(stage.GetEndTimeCode())
        except Exception:
            start_tc, end_tc = 0.0, 0.0
        try:
            stage_ident = stage.GetRootLayer().identifier
        except Exception:
            stage_ident = "?"
        print(
            f"{_PRINT_PREFIX} init prim={self.prim_path} offscreen_stage={stage_ident} "
            f"source={asset_path} tps={tcps}(forced={LAM_FIXED_FPS}) "
            f"timeCode=[{start_tc},{end_tc}]",
            flush=True,
        )
        return True

    def setup_master_mirror_prim(self) -> bool:
        """Master stage 에 mirror prim 이 존재하도록 보장.

        현재 LAM 의 multi_usd_loader 가 이미 reference 로 prim 을 author 하고 있으므로
        대부분의 경우 prim 은 이미 존재한다(`stage.GetPrimAtPath(prim_path)` valid).
        Phase A 골격에서는 prim 존재만 확인하고, 없으면 빈 Xform 으로 생성한다.

        Phase B/B-2-a 권장 경로 (asset reference + freeze sublayer) 에서는 prim 이
        reference 로 이미 author 되어 있으므로 본 함수는 단순 검증만 수행.

        반환: prim 이 유효하거나 새로 만들어진 경우 True, master 가 set 안 됐거나
        실패한 경우 False.
        """
        if self._master_stage is None:
            return False
        if _Usd is None:
            return False

        try:
            prim = self._master_stage.GetPrimAtPath(self.prim_path)
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} setup_master_mirror_prim GetPrimAtPath FAIL "
                f"prim={self.prim_path} exc={exc}",
                flush=True,
            )
            return False

        if prim and prim.IsValid():
            return True

        # prim 이 없으면 빈 Xform 으로 만들기만 (자산 reference 는 multi_usd_loader 가
        # 별도로 author 하므로 여기서는 만들지 않는다 — Phase A 단독 호출 시에만 의미).
        try:
            from pxr import UsdGeom  # type: ignore

            xf = UsdGeom.Xform.Define(self._master_stage, self.prim_path)
            ok = bool(xf and xf.GetPrim() and xf.GetPrim().IsValid())
            if ok:
                print(
                    f"{_PRINT_PREFIX} setup_master_mirror_prim DEFINED Xform "
                    f"prim={self.prim_path}",
                    flush=True,
                )
            return ok
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} setup_master_mirror_prim Define FAIL "
                f"prim={self.prim_path} exc={exc}",
                flush=True,
            )
            return False

    # ------------------------------------------------------------------ evaluate

    def evaluate_and_write(self, virtual_time: Optional[float] = None) -> int:
        """1 frame 평가: offscreen stage 에서 자기 vt 의 attribute 값을 읽고
        master mirror attribute 에 default value 로 write.

        Args:
            virtual_time: 평가할 가상 시간 (초). None 이면 `instance.virtual_time` 사용.

        Returns:
            이번 호출에서 실제로 write 된 attribute 개수. 0 이면 평가 대상이 없거나
            stage 가 미준비 상태.
        """
        if not self.is_ready:
            self._last_wrote = 0
            return 0

        # 평가 시각 결정.
        vt = float(virtual_time) if virtual_time is not None else float(self._instance.virtual_time)
        vt += float(self._instance.offset_sec)
        self._last_virtual_time = vt

        # FPS 30 고정 — 자산 헤더 / inst.asset_tps 모두 무시하고 LAM_FIXED_FPS 사용.
        tps = LAM_FIXED_FPS
        timecode = vt * float(tps)

        # 1) 캐시 보장.
        if not self._attr_cache_built:
            self._build_attr_cache()

        # 2) offscreen stage time 을 **항상** 먼저 맞춘다 — 일반 attr/Skel 모두 동일 전제.
        try:
            self._offscreen_stage.SetEditTarget(
                _Usd.EditTarget(self._offscreen_stage.GetRootLayer())
            )
        except Exception:
            pass
        try:
            self._offscreen_stage.SetCurrentTimeCode(float(timecode))
        except Exception:
            pass

        # 3) 일반 timeSamples attribute 경로.
        wrote = 0
        for entry in self._attr_cache:
            tc = timecode
            if entry.max_tc >= entry.min_tc:
                if tc < entry.min_tc:
                    tc = entry.min_tc
                elif tc > entry.max_tc:
                    tc = entry.max_tc

            try:
                val = entry.attr.Get(tc)
            except Exception:
                continue
            if val is None:
                continue

            try:
                # default value 로 write — master stage 는 timeCode 진행 안 하므로
                # root layer 의 default 가 reference 의 timeSamples 보다 winner.
                entry.mirror_attr.Set(val)
                wrote += 1
            except Exception:
                continue

        # 4) attribute timeSamples 가 없는 FBX→USD 자산 — UsdSkel 로 관절 포즈 평가 후
        #    master 의 SkelAnimation 에 default 로 기록 (Option E / Phase C 최소).
        if wrote == 0:
            wrote = self._evaluate_skel_and_write_to_master(float(timecode))

        self._last_wrote = wrote

        if not self._attr_cache and wrote == 0 and not self._diag_first_eval_warned_zero:
            self._diag_first_eval_warned_zero = True
            print(
                f"{_PRINT_PREFIX} evaluate WARN no attr samples and Skel write failed "
                f"prim={self.prim_path} — diag: payload 로드 / UsdSkel 바인딩 확인",
                flush=True,
            )

        # 진단 — 인스턴스 첫 1 회만 출력 (이후는 조용히, evaluator 의 update 빈도 보호).
        if not self._diag_first_eval_logged:
            self._diag_first_eval_logged = True
            print(
                f"{_PRINT_PREFIX} update prim={self.prim_path} "
                f"virtual_time={vt:.3f}s timeCode={timecode:.3f} "
                f"attrs={len(self._attr_cache)} wrote={wrote}",
                flush=True,
            )

        return wrote

    # ------------------------------------------------------------------ dispose

    def dispose(self) -> None:
        """Offscreen stage 해제 + attribute 캐시 폐기.

        master mirror prim 자체는 본 runtime 이 만든 게 아니므로 (multi_usd_loader 가
        author 한 reference 위에 평가만 했으므로) 제거하지 않는다.

        본 호출 후 같은 객체에 다시 setup 을 호출해도 동작 가능 — 단순 reset 과 동등.
        """
        self._release_offscreen_stage()
        self._invalidate_attr_cache()
        self._last_wrote = 0
        self._last_virtual_time = float("nan")
        self._diag_first_eval_logged = False
        self._diag_first_eval_warned_zero = False
        self._offscreen_root_path_str = ""
        self._skel_cache = None
        self._skel_write_diag_logged = False
        self._offscreen_open_failed_key = ""
        self._lam_option_e_setup_fail_logged = False
        print(f"{_PRINT_PREFIX} dispose prim={self.prim_path}", flush=True)

    # ------------------------------------------------------------------ internals

    def _release_offscreen_stage(self) -> None:
        """offscreen stage 참조를 풀어 GC 가 회수 가능하도록 함.

        pxr.Usd.Stage 는 명시적 Close API 가 없으므로 ref 만 끊는다.
        """
        if self._offscreen_stage is not None:
            try:
                ident = self._offscreen_stage.GetRootLayer().identifier
            except Exception:
                ident = "?"
            print(
                f"{_PRINT_PREFIX} release offscreen prim={self.prim_path} stage={ident}",
                flush=True,
            )
        self._offscreen_stage = None
        self._offscreen_asset_path = ""

    def _invalidate_attr_cache(self) -> None:
        self._attr_cache = []
        self._attr_cache_built = False
        self._offscreen_root_path_str = ""

    def _load_all_payloads(self, stage) -> int:
        """stage 전체에서 payload 가 있는 prim 을 모두 Load 한다.

        반환: Load 호출 시도 횟수(페이로드 보유 prim 개수; 실제 신규 로드와는 다를 수 있음).
        """
        n = 0
        if stage is None:
            return 0
        try:
            for prim in stage.Traverse():
                try:
                    if prim.HasPayload():
                        prim.Load()
                        n += 1
                except Exception:
                    continue
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} load_all_payloads exc prim={self.prim_path} exc={exc}",
                flush=True,
            )
        return n

    def _map_offscreen_path_to_master(self, off_path_str: str) -> str:
        """offscreen 경로 → master 인스턴스 prim 아래 동일 상대 경로."""
        base = (self._offscreen_root_path_str or "").rstrip("/")
        op = off_path_str.replace("\\", "/")
        if base and op.startswith(base):
            suf = op[len(base) :]
            if not suf.startswith("/"):
                suf = "/" + suf
            return (self.prim_path.rstrip("/") + suf).replace("//", "/")
        # fallback — 동일 suffix 를 붙일 수 없으면 인스턴스 루트만.
        return self.prim_path

    def _evaluate_skel_and_write_to_master(self, timecode: float) -> int:
        """UsdSkel — Skeleton 의 관절 로컬 행렬을 평가해 SkelAnimation TRS 를 master 에 기록.

        FBX→USD 변환본은 mesh transform timeSamples 없이 SkelAnimation 만 있는 경우가
        많다(`animatable_attr_count=0`). 이 경로가 없으면 Option E 가 영구히 wrote=0.
        """
        try:
            from pxr import UsdSkel  # type: ignore
        except Exception:
            return 0

        stage = self._offscreen_stage
        master = self._master_stage
        if stage is None or master is None or _Usd is None:
            return 0

        if not self._offscreen_root_path_str:
            return 0

        off_root = stage.GetPrimAtPath(self._offscreen_root_path_str)
        if not off_root or not off_root.IsValid():
            return 0

        if self._skel_cache is None:
            self._skel_cache = UsdSkel.Cache()

        cache = self._skel_cache
        try:
            cache.Populate(off_root)
        except Exception as exc:
            if not self._skel_write_diag_logged:
                print(
                    f"{_PRINT_PREFIX} UsdSkel.Cache.Populate FAIL prim={self.prim_path} "
                    f"exc={exc}",
                    flush=True,
                )
                self._skel_write_diag_logged = True
            return 0

        tc = _Usd.TimeCode(float(timecode))
        wrote = 0

        try:
            for prim in stage.Traverse():
                if not prim.IsA(UsdSkel.Skeleton):
                    continue
                skel_q = cache.GetSkelQuery(prim)
                if skel_q is None or not skel_q.IsValid():
                    continue
                try:
                    xforms = skel_q.ComputeJointLocalTransforms(tc)
                except Exception:
                    continue
                if xforms is None or len(xforms) == 0:
                    continue

                try:
                    decomp = UsdSkel.DecomposeTransforms(xforms)
                except Exception:
                    continue

                # pxr 바인딩: (ok, translations, scales, rotations) 또는 유사 튜플.
                translations = scales = rotations = None
                ok_d = True
                if isinstance(decomp, (list, tuple)):
                    if len(decomp) >= 4:
                        ok_d = bool(decomp[0])
                        translations, scales, rotations = decomp[1], decomp[2], decomp[3]
                    elif len(decomp) == 3:
                        translations, scales, rotations = decomp[0], decomp[1], decomp[2]
                if not ok_d or translations is None:
                    continue

                targets: List[Any] = []
                try:
                    _api = UsdSkel.BindingAPI(prim)
                    _rel = _api.GetAnimationSourceRel() if _api else None
                    if _rel:
                        targets = list(_rel.GetTargets() or [])
                except Exception:
                    targets = []
                if not targets:
                    for rel_name in ("skel:animationSource", "skel:animation"):
                        try:
                            r2 = prim.GetRelationship(rel_name)
                            if r2:
                                targets = list(r2.GetTargets() or [])
                                if targets:
                                    break
                        except Exception:
                            continue

                if not targets:
                    continue

                anim_off_path = str(targets[0])
                anim_off = stage.GetPrimAtPath(anim_off_path)
                if not anim_off or not anim_off.IsValid():
                    continue

                mas_anim_path = self._map_offscreen_path_to_master(anim_off_path)
                try:
                    mas_anim = master.OverridePrim(mas_anim_path)
                except Exception:
                    mas_anim = master.GetPrimAtPath(mas_anim_path)

                if not mas_anim or not mas_anim.IsValid():
                    continue

                pairs = [
                    ("translations", translations),
                    ("scales", scales),
                    ("rotations", rotations),
                ]
                chunk_wrote = 0
                for logical_name, val in pairs:
                    if val is None:
                        continue
                    try:
                        src_attr = anim_off.GetAttribute(logical_name)
                        if not src_attr or not src_attr.IsValid():
                            continue
                        type_name = src_attr.GetTypeName()
                        dst_attr = mas_anim.GetAttribute(logical_name)
                        if not dst_attr or not dst_attr.IsValid():
                            dst_attr = mas_anim.CreateAttribute(
                                logical_name, type_name, custom=False
                            )
                        if dst_attr:
                            dst_attr.Set(val)
                            chunk_wrote += 1
                    except Exception:
                        continue

                if chunk_wrote > 0:
                    wrote += chunk_wrote
                    if not self._skel_write_diag_logged:
                        self._skel_write_diag_logged = True
                        print(
                            f"{_PRINT_PREFIX} skel TRS write OK prim={self.prim_path} "
                            f"skel={prim.GetPath()} anim_master={mas_anim_path} "
                            f"timeCode={timecode:.3f} attrs_written={chunk_wrote}",
                            flush=True,
                        )
                    return wrote

        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} skel evaluate exc prim={self.prim_path} exc={exc}",
                flush=True,
            )
        return wrote

    def _build_attr_cache(self) -> None:
        """Offscreen stage 의 prim 산하에서 timeSamples 가 있는 attribute 만 추출하여
        master mirror attribute 와 1:1 매핑.

        - prim_path 기준이 offscreen / master 양쪽에서 같다는 전제 (multi_usd_loader 가
          reference 로 attach 한 위치 = master 의 prim path = offscreen 의 default prim
          path 또는 그 산하 동일 path).
        - offscreen 의 default prim 이 master 의 reference primPath 와 다른 경우는
          Phase B 의 reference 매핑 정책으로 보강한다(현재 Phase A 골격은 단순 매핑).
        - master 측에 동일 이름 자식이 없으면 `OverridePrim` 으로 mirror 자식을 자동 생성
          (reference 안에 없는 sibling 까지 traverse 가능하게 함).
        """
        self._attr_cache = []
        self._attr_cache_built = True
        self._offscreen_root_path_str = ""

        if self._offscreen_stage is None or self._master_stage is None or _Usd is None:
            return

        # offscreen 의 root prim 결정 — Phase B-2-a 보강:
        #  - defaultPrim 우선
        #  - 없으면 pseudo-root 의 첫 자식
        #  - 그래도 동물원처럼 sibling 에 animation 이 흩어진 자산은 cache build 가 0 일
        #    가능성이 있으므로, 0 일 때 본 함수 끝에서 offscreen 전체 stage 를 traverse
        #    하는 fallback 진단을 수행.
        offscreen_root_prim = None
        try:
            dp = self._offscreen_stage.GetDefaultPrim()
            if dp and dp.IsValid():
                offscreen_root_prim = dp
        except Exception:
            offscreen_root_prim = None

        if offscreen_root_prim is None:
            try:
                pseudo = self._offscreen_stage.GetPseudoRoot()
                for ch in pseudo.GetAllChildren():
                    offscreen_root_prim = ch
                    break
            except Exception:
                offscreen_root_prim = None

        if offscreen_root_prim is None:
            print(
                f"{_PRINT_PREFIX} cache no offscreen root prim={self.prim_path} "
                f"asset={self._offscreen_asset_path}",
                flush=True,
            )
            return

        try:
            master_root_prim = self._master_stage.GetPrimAtPath(self.prim_path)
        except Exception:
            master_root_prim = None
        # 2026-05-14 — drag&drop 으로 자산이 inst 직속이 아닌 자식 경로에 박힌 경우,
        # offscreen `/Root` 트리는 master 의 **mirror_root_prim_path** 아래에만 매핑한다.
        # 비어 있으면 기존과 동일하게 `self.prim_path` (= 인스턴스 등록 prim).
        try:
            mr_path = (getattr(self._instance, "mirror_root_prim_path", "") or "").strip()
        except Exception:
            mr_path = ""
        if mr_path:
            try:
                alt = self._master_stage.GetPrimAtPath(mr_path)
            except Exception:
                alt = None
            if alt and alt.IsValid():
                master_root_prim = alt
                print(
                    f"{_PRINT_PREFIX} cache mirror_root={mr_path} (inst={self.prim_path})",
                    flush=True,
                )
                # 2026-05-14 보강 — offscreen 측 root 도 동일 delta 만큼 진입할 수 있다.
                # 예) Extract 의 anonymous layer 는 `/Root/test1/N_07_Laser_Cutting/...` 처럼
                # master inst 트리 전체를 그대로 복사한 형태. 이 경우 offscreen `/Root` 대신
                # `/Root/test1/N_07_Laser_Cutting` 으로 진입해야 master mirror_root 와 1:1
                # 매핑된다 (그렇지 않으면 master 측에서 `mirror_root/test1/N_07_*` 자식이
                # OverridePrim 으로 다시 author 되어 트리가 복제된다).
                #
                # Bake 의 baked layer 는 default prim 자체가 자산 root (`N_07_Laser_Cutting`)
                # 라서 offscreen 측에는 delta path 가 존재하지 않는다 — 그 경우 offscreen
                # root 를 그대로 유지한다.
                try:
                    delta = ""
                    if mr_path.startswith(self.prim_path.rstrip("/") + "/"):
                        delta = mr_path[len(self.prim_path.rstrip("/")) + 1:]
                    if delta:
                        try:
                            cand_path = (
                                str(offscreen_root_prim.GetPath()).rstrip("/")
                                + "/"
                                + delta
                            )
                            cand = self._offscreen_stage.GetPrimAtPath(cand_path)
                        except Exception:
                            cand = None
                        if cand and cand.IsValid():
                            offscreen_root_prim = cand
                            print(
                                f"{_PRINT_PREFIX} cache offscreen entered delta={delta!r} "
                                f"-> {cand.GetPath()} (mirrors master {mr_path})",
                                flush=True,
                            )
                except Exception as _delta_exc:
                    print(
                        f"{_PRINT_PREFIX} cache offscreen delta entry exc={_delta_exc}",
                        flush=True,
                    )
            else:
                print(
                    f"{_PRINT_PREFIX} cache WARN mirror_root invalid={mr_path!r} "
                    f"— fallback inst={self.prim_path}",
                    flush=True,
                )
        if not master_root_prim or not master_root_prim.IsValid():
            print(
                f"{_PRINT_PREFIX} cache no master mirror prim_path={self.prim_path} "
                f"(setup_master_mirror_prim 을 먼저 호출했는지 확인)",
                flush=True,
            )
            return

        try:
            off_root_type = offscreen_root_prim.GetTypeName()
        except Exception:
            off_root_type = "?"
        try:
            off_root_path = str(offscreen_root_prim.GetPath())
        except Exception:
            off_root_path = "?"
        try:
            mas_root_type = master_root_prim.GetTypeName()
        except Exception:
            mas_root_type = "?"
        try:
            mas_child_names = [c.GetName() for c in master_root_prim.GetAllChildren()]
        except Exception:
            mas_child_names = []
        try:
            mas_root_path = str(master_root_prim.GetPath())
        except Exception:
            mas_root_path = self.prim_path
        print(
            f"{_PRINT_PREFIX} cache map off_root={off_root_path}({off_root_type}) "
            f"-> master={mas_root_path}({mas_root_type}) master_children={mas_child_names}",
            flush=True,
        )

        self._offscreen_root_path_str = off_root_path

        skipped_children: List[str] = []

        try:
            stack = [(offscreen_root_prim, master_root_prim)]
            seen: set = set()
            while stack:
                off_prim, mas_prim = stack.pop()
                try:
                    off_path_str = str(off_prim.GetPath())
                except Exception:
                    continue
                if off_path_str in seen:
                    continue
                seen.add(off_path_str)

                try:
                    off_children = list(off_prim.GetAllChildren())
                except Exception:
                    off_children = []
                for off_ch in off_children:
                    try:
                        ch_name = off_ch.GetName()
                    except Exception:
                        continue
                    mas_ch = None
                    try:
                        mas_ch = mas_prim.GetChild(ch_name)
                    except Exception:
                        mas_ch = None
                    if mas_ch and mas_ch.IsValid():
                        stack.append((off_ch, mas_ch))
                        continue
                    # master 쪽에 자식이 없는 케이스 — OverridePrim 으로 자동 생성.
                    try:
                        try:
                            mas_prim_path_str = str(mas_prim.GetPath())
                        except Exception:
                            mas_prim_path_str = self.prim_path
                        new_path = mas_prim_path_str.rstrip("/") + "/" + ch_name
                        over_prim = self._master_stage.OverridePrim(new_path)
                        if over_prim and over_prim.IsValid():
                            stack.append((off_ch, over_prim))
                            continue
                    except Exception as exc:
                        skipped_children.append(f"{ch_name}(override_exc={exc})")
                        continue
                    skipped_children.append(ch_name)

                try:
                    attrs = list(off_prim.GetAttributes())
                except Exception:
                    attrs = []
                for attr in attrs:
                    try:
                        n_ts = attr.GetNumTimeSamples()
                    except Exception:
                        continue
                    if n_ts <= 0:
                        continue
                    try:
                        bracket = attr.GetTimeSamplesInInterval(
                            _Usd.Interval.GetFullInterval()
                        )
                    except Exception:
                        bracket = []
                    if bracket:
                        mn = float(bracket[0])
                        mx = float(bracket[-1])
                    else:
                        try:
                            ts = attr.GetTimeSamples()
                            mn = float(ts[0])
                            mx = float(ts[-1])
                        except Exception:
                            mn, mx = 0.0, 0.0

                    try:
                        name = attr.GetName()
                    except Exception:
                        continue
                    try:
                        mirror_attr = mas_prim.GetAttribute(name)
                    except Exception:
                        mirror_attr = None
                    if (mirror_attr is None) or (not getattr(mirror_attr, "IsValid", lambda: False)()):
                        try:
                            type_name = attr.GetTypeName()
                            mirror_attr = mas_prim.CreateAttribute(name, type_name, custom=False)
                        except Exception:
                            mirror_attr = None
                    if mirror_attr is None:
                        continue
                    self._attr_cache.append(
                        _AttrSampleEntry(
                            attr=attr,
                            mirror_attr=mirror_attr,
                            min_tc=mn,
                            max_tc=mx,
                        )
                    )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} cache build exc prim={self.prim_path} exc={exc}",
                flush=True,
            )

        if skipped_children:
            print(
                f"{_PRINT_PREFIX} cache children NOT_FOUND_IN_MASTER prim={self.prim_path} "
                f"count={len(skipped_children)} names={skipped_children[:20]}",
                flush=True,
            )

        print(
            f"{_PRINT_PREFIX} cache built prim={self.prim_path} attrs={len(self._attr_cache)}",
            flush=True,
        )

        if self._attr_cache:
            try:
                by_prim: Dict[str, int] = defaultdict(int)
                tmn: Optional[float] = None
                tmx: Optional[float] = None
                for ent in self._attr_cache:
                    try:
                        pp = str(ent.attr.GetPrim().GetPath())
                    except Exception:
                        continue
                    by_prim[pp] += 1
                    try:
                        tmn = (
                            ent.min_tc
                            if tmn is None or ent.min_tc < tmn
                            else tmn
                        )
                        tmx = (
                            ent.max_tc
                            if tmx is None or ent.max_tc > tmx
                            else tmx
                        )
                    except Exception:
                        pass
                top = sorted(by_prim.items(), key=lambda kv: -kv[1])[:20]
                print(
                    f"{_PRINT_PREFIX} TS replay path coverage prim={self.prim_path} "
                    f"offscreen_prims_touched={len(by_prim)} attrs_cached={len(self._attr_cache)} "
                    f"union_tc=[{tmn},{tmx}] top_prims_by_attr_count={top!r}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} TS replay coverage log failed: {exc}",
                    flush=True,
                )

        # cache 가 0 이면 자산의 어디에 timeSamples 가 있는지를 추가 dump — 다음 patch 의
        # 매핑 정책을 결정하기 위한 핵심 진단.
        if not self._attr_cache:
            self._diag_dump_offscreen_animatable()

    def _diag_dump_offscreen_animatable(self) -> None:
        """Offscreen stage 전체를 traverse 하면서 timeSamples 가 있는 모든 attribute 의
        path/sample 개수를 출력. attrs_cached=0 일 때 자산 구조 식별용 진단.

        Phase B-2-a 의 자산 구조 케이스 분기를 위한 정보 수집 단계 — 출력만 한다.
        """
        if self._offscreen_stage is None or _Usd is None:
            return
        # 진단 직전에 payload 한 번 더 (첫 cache build 시점과 타이밍 차이 보강).
        npl = self._load_all_payloads(self._offscreen_stage)
        if npl > 0:
            print(
                f"{_PRINT_PREFIX} diag payload reload prim={self.prim_path} count={npl}",
                flush=True,
            )

        try:
            from pxr import UsdSkel  # type: ignore

            n_skel_root = n_skel = n_anim = 0
            for p in self._offscreen_stage.Traverse():
                try:
                    if p.IsA(UsdSkel.Root):
                        n_skel_root += 1
                    if p.IsA(UsdSkel.Skeleton):
                        n_skel += 1
                    if p.IsA(UsdSkel.Animation):
                        n_anim += 1
                except Exception:
                    continue
            print(
                f"{_PRINT_PREFIX} diag skel counts prim={self.prim_path} "
                f"SkelRoot={n_skel_root} Skeleton={n_skel} SkelAnimation={n_anim}",
                flush=True,
            )
        except Exception:
            pass

        # 자산 구조 진단 — 어떤 타입이 몇 개나 있는지, instance/payload/reference 비율,
        # PointInstancer / OmniGraph 가 있는지 등을 1 회 dump.
        try:
            from collections import Counter

            n_total = 0
            type_counts: Counter = Counter()
            n_instance = n_instanceable = n_in_prototype = 0
            n_has_refs = n_has_pay = 0
            point_instancer_paths: List[str] = []
            omnigraph_paths: List[str] = []
            xformop_anim_paths: List[str] = []  # xformOp:transform 등 transform attr 보유
            for p in self._offscreen_stage.Traverse(
                _Usd.PrimAllPrimsPredicate
                if hasattr(_Usd, "PrimAllPrimsPredicate")
                else _Usd.PrimDefaultPredicate
            ):
                n_total += 1
                try:
                    tn = str(p.GetTypeName())
                    type_counts[tn] += 1
                except Exception:
                    tn = ""
                try:
                    if p.IsInstance():
                        n_instance += 1
                    if getattr(p, "IsInstanceable", None) and p.IsInstanceable():
                        n_instanceable += 1
                    if getattr(p, "IsInPrototype", None) and p.IsInPrototype():
                        n_in_prototype += 1
                except Exception:
                    pass
                try:
                    if p.HasAuthoredReferences():
                        n_has_refs += 1
                    if p.HasAuthoredPayloads():
                        n_has_pay += 1
                except Exception:
                    pass
                if tn == "PointInstancer" and len(point_instancer_paths) < 5:
                    try:
                        point_instancer_paths.append(str(p.GetPath()))
                    except Exception:
                        pass
                if (
                    tn in {"OmniGraph", "PushGraph", "OmniGraphFunction"}
                    or tn.startswith("OG")
                ) and len(omnigraph_paths) < 5:
                    try:
                        omnigraph_paths.append(f"{p.GetPath()}({tn})")
                    except Exception:
                        pass
                # transform 류 attribute timeSamples 가 있는지 직접 확인 — 일부 자산은
                # xformOp:transform 에 timeSamples 가 박혀있고, 이건 위 표준 traverse 가
                # 모두 잡지만 만약 합성 단계에서 prototype 안에 있다면 안 잡힌다.
                try:
                    for a in p.GetAttributes():
                        an = a.GetName()
                        if not an.startswith("xformOp:") and an not in (
                            "transform",
                            "matrix",
                        ):
                            continue
                        if a.GetNumTimeSamples() > 0 and len(xformop_anim_paths) < 10:
                            xformop_anim_paths.append(
                                f"{p.GetPath()}.{an}(n={a.GetNumTimeSamples()})"
                            )
                except Exception:
                    continue
            print(
                f"{_PRINT_PREFIX} diag struct prim={self.prim_path} total_prims={n_total} "
                f"instance={n_instance} instanceable={n_instanceable} "
                f"in_prototype={n_in_prototype} has_refs={n_has_refs} has_payloads={n_has_pay}",
                flush=True,
            )
            top = type_counts.most_common(12)
            print(
                f"{_PRINT_PREFIX} diag types prim={self.prim_path} top={top}",
                flush=True,
            )
            if point_instancer_paths:
                print(
                    f"{_PRINT_PREFIX} diag PointInstancer prim={self.prim_path} "
                    f"paths={point_instancer_paths}",
                    flush=True,
                )
            if omnigraph_paths:
                print(
                    f"{_PRINT_PREFIX} diag OmniGraph prim={self.prim_path} "
                    f"paths={omnigraph_paths} — 이 자산은 OmniGraph 런타임 그래프로 "
                    f"애니메이션을 구동할 가능성. timeSamples 가 자산에 없을 수 있음.",
                    flush=True,
                )
            if xformop_anim_paths:
                print(
                    f"{_PRINT_PREFIX} diag xformOp timeSamples prim={self.prim_path} "
                    f"paths={xformop_anim_paths}",
                    flush=True,
                )

            # prototype 안에 timeSamples 가 있는지도 별도 dump — instance/prototype 케이스.
            try:
                protos = self._offscreen_stage.GetPrototypes()
            except Exception:
                protos = []
            n_proto_animatable = 0
            proto_lines: List[str] = []
            for proto in protos or []:
                try:
                    for p in _Usd.PrimRange(
                        proto, _Usd.PrimAllPrimsPredicate
                    ) if hasattr(_Usd, "PrimAllPrimsPredicate") else proto.GetAllChildren():
                        try:
                            for a in p.GetAttributes():
                                n = a.GetNumTimeSamples()
                                if n > 0:
                                    n_proto_animatable += 1
                                    if len(proto_lines) < 10:
                                        proto_lines.append(
                                            f"{a.GetPath()}(n={n})"
                                        )
                        except Exception:
                            continue
                except Exception:
                    continue
            if n_proto_animatable > 0 or protos:
                print(
                    f"{_PRINT_PREFIX} diag prototypes prim={self.prim_path} "
                    f"protos={len(list(protos) if protos else [])} "
                    f"animatable_in_protos={n_proto_animatable} samples={proto_lines}",
                    flush=True,
                )
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} diag struct exc prim={self.prim_path} exc={exc}",
                flush=True,
            )

        lines: List[str] = []
        try:
            for prim in self._offscreen_stage.Traverse():
                try:
                    attrs = list(prim.GetAttributes())
                except Exception:
                    continue
                for attr in attrs:
                    try:
                        n = attr.GetNumTimeSamples()
                    except Exception:
                        n = 0
                    if n > 0:
                        try:
                            lines.append(f"  {attr.GetPath()} samples={n}")
                        except Exception:
                            pass
        except Exception as exc:
            print(
                f"{_PRINT_PREFIX} diag dump exc prim={self.prim_path} exc={exc}",
                flush=True,
            )
            return
        print(
            f"{_PRINT_PREFIX} diag dump offscreen prim={self.prim_path} "
            f"animatable_attr_count={len(lines)}",
            flush=True,
        )
        for line in lines[:30]:
            print(line, flush=True)
        if len(lines) > 30:
            print(f"  ... and {len(lines) - 30} more.", flush=True)


__all__ = ["AnimationInstanceRuntime"]

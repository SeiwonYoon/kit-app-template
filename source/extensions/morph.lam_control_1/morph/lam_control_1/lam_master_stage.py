"""L1-a — Master Stage 컨테이너 + REQ-005 영속성 정책.

본 모듈의 책임:
- LAM 전용 USD context(`morph_lam_master`) 를 1개 보유.
- 새 master stage 생성(in-memory) / 기존 master.usd 열기 / Save Master 처리.
- **모든 author 는 root layer 를 edit target 으로 한다(REQ-005 P-3).**
- master 파일 미저장 상태에서도 인스턴스 등록 / reauthor 모두 가능(저장만 안 될 뿐).

본 모듈은 `omni.timeline` 을 import 하지 않는다(REQ-004 §12 보호 영역).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional


_PRINT_PREFIX = "[LAM/L1a]"


_SAFE_TAG_RE = re.compile(r"[^A-Za-z0-9_]")


def _slug_for_tag(s: str) -> str:
    """LAM sublayer identifier 에 들어갈 안전 태그(영숫자/언더스코어)."""
    s = _SAFE_TAG_RE.sub("_", s or "").strip("_")
    if not s:
        s = "anon"
    return s

# REQ-007 결정 A' (2026-05-10) — LAM 도 default 컨텍스트(`""`) 를 사용한다.
# 이유: 사용자 환경의 일부 Kit 빌드에서 `viewport.usd_context_name` setter 가 silent 하게
# 무시되어 default viewport·Stage 패널·Property 패널이 LAM 의 prim 을 못 보는 문제가
# 발생. default 컨텍스트를 같이 쓰면 모든 Kit 기본 패널이 자동으로 LAM 의 author 를 본다.
# 트레이드오프: tbs_control_1 의 USD Load 가 default stage 를 새로 열면 LAM 의 author 도
# 같이 사라진다(REQ-007 §트레이드오프). 사용자가 명시적으로 누른 경우만 발생.
LAM_MASTER_CONTEXT_NAME = ""


class MasterStage:
    """LAM master stage 의 라이프사이클 + 저장/로드.

    Phase 0 단계에서는 **omni.usd 가 import 되지 않은 환경에서도 모듈이 import 가능** 해야 하므로
    omni.usd 사용은 모두 lazy import + 실패 허용.
    실제 author 동작은 USD 가 사용 가능할 때만 실행된다.
    """

    def __init__(self, context_name: Optional[str] = None) -> None:
        self._context_name: str = (
            str(context_name).strip() if context_name is not None else LAM_MASTER_CONTEXT_NAME
        )
        self._master_path: str = ""    # 저장된 master.usd 파일 경로(없으면 빈 문자열)
        self._anonymous: bool = True   # in-memory 익명 layer 사용 여부
        # 핫픽스 7 (Opt-1) — 인스턴스 prim_path → 그 인스턴스 전용 anonymous Sdf.Layer.
        # root layer 의 subLayerPaths 의 가장 앞쪽(=stronger 슬롯) 에 삽입되어,
        # 그 안에서 author 한 reference 가 무조건 winner 가 된다.
        # 참고: pxr.Sdf 미가용(테스트 환경) 시에도 Window 가 import 되어야 하므로
        #       Sdf 는 lazy import 한다.
        self._inst_sublayers: Dict[str, "object"] = {}  # value 는 Sdf.Layer 객체

    # ------------------------------------------------------------------ public

    @property
    def context_name(self) -> str:
        return self._context_name

    @property
    def master_path(self) -> str:
        return self._master_path

    @property
    def is_anonymous(self) -> bool:
        return self._anonymous

    def ensure_context(self) -> bool:
        """LAM 컨텍스트(현재는 default `""`)가 존재하도록 보장. stage 가 없으면 새로 생성.

        REQ-007 결정 A' 이후 본 메서드는:
        - default 컨텍스트를 그대로 사용한다(빈 문자열이면 `omni.usd.get_context("")`).
        - default 의 stage 가 비어 있으면 새 stage 를 만들고 upAxis 를 명시한다(Z-up).
        - 이미 stage 가 있으면 그대로 둔다(TBS 가 만든 stage 위에 LAM 이 author 가능).
        - **stage 가 새로 만들어졌든 기존 것이든 LAM 의 FPS 30 고정 정책을 stage 와
          `omni.timeline` 의 target framerate 양쪽에 강제 적용**한다(사용자 요구
          2026-05-11: 하단 타임라인 창 fps=60 → 30).
        """
        try:
            import omni.usd as ou  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.usd not available: {exc}", flush=True)
            return False

        ctx = ou.get_context(self._context_name)
        if ctx is None:
            # 빈 이름("")은 default — 항상 존재하므로 None 이 거의 안 나옴.
            # 그래도 안전하게 명시적 컨텍스트만 create_context 호출.
            if self._context_name:
                try:
                    ctx = ou.create_context(self._context_name)
                except Exception as exc:
                    print(f"{_PRINT_PREFIX} create_context failed: {exc}", flush=True)
                    return False
            else:
                print(f"{_PRINT_PREFIX} default context unavailable", flush=True)
                return False

        # 비어 있으면 새 stage 생성 + upAxis 를 표준 Z-up 으로 author.
        if ctx.get_stage() is None:
            try:
                # 새 stage 를 만들기 전에 이전 stage 의 인스턴스 sublayer 들은 모두 청소.
                self._inst_sublayers.clear()
                ctx.new_stage()
                self._anonymous = True
                self._master_path = ""
                # 새로 만든 stage 는 Z-up 으로 명시(자산 USD 가 Y-up 이면 add_usd 가 보정).
                try:
                    from pxr import UsdGeom  # type: ignore

                    UsdGeom.SetStageUpAxis(ctx.get_stage(), UsdGeom.Tokens.z)
                except Exception as exc:
                    print(f"{_PRINT_PREFIX} SetStageUpAxis failed: {exc}", flush=True)
            except Exception as exc:
                print(f"{_PRINT_PREFIX} new_stage failed: {exc}", flush=True)
                return False

        self.force_fixed_fps_30()
        return True

    def open_master(self, path: str) -> bool:
        """기존 master.usd 를 LAM 컨텍스트에 로드(REQ-005 로드 흐름).

        ``path`` 는 로컬 ``.usd`` 파일 또는 ``omniverse://`` Nucleus URL.
        """
        from .lam_usd_path import master_usd_path_is_openable

        path = (path or "").strip()
        if not master_usd_path_is_openable(path):
            print(
                f"{_PRINT_PREFIX} open_master: not openable "
                f"(로컬 .usd 또는 omniverse://…): {path!r}",
                flush=True,
            )
            return False

        try:
            import omni.usd as ou  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.usd not available: {exc}", flush=True)
            return False

        if not self.ensure_context():
            return False

        ctx = ou.get_context(self._context_name)
        # open 전에 기존 인스턴스 sublayer 캐시는 모두 비움(이전 stage 와 무관해짐).
        self._inst_sublayers.clear()
        try:
            ok = ctx.open_stage(path)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} open_stage failed: {exc}", flush=True)
            return False

        if ok:
            self._master_path = path
            self._anonymous = False
            print(f"{_PRINT_PREFIX} open_master OK path={path}", flush=True)
        # open 직후에도 fps 30 강제 (불러온 파일에 60fps 메타가 있어도 정책상 30 으로 통일).
        self.force_fixed_fps_30()
        return bool(ok)

    def save_master(self, path: str) -> bool:
        """현재 stage 를 사용자가 지정한 경로로 저장(REQ-005 저장 흐름)."""
        if not path:
            return False
        try:
            import omni.usd as ou  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} omni.usd not available: {exc}", flush=True)
            return False

        ctx = ou.get_context(self._context_name)
        if ctx is None or ctx.get_stage() is None:
            print(f"{_PRINT_PREFIX} save_master: no stage", flush=True)
            return False

        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            stage = ctx.get_stage()
            stage.GetRootLayer().Export(path)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} save_master failed: {exc}", flush=True)
            return False

        self._master_path = path
        self._anonymous = False
        print(f"{_PRINT_PREFIX} save_master OK path={path}", flush=True)
        return True

    def get_stage(self):  # type: ignore[override]
        """현재 stage 를 반환(없으면 None). USD 미가용 환경에서 None."""
        try:
            import omni.usd as ou  # type: ignore
        except Exception:
            return None
        ctx = ou.get_context(self._context_name)
        if ctx is None:
            return None
        return ctx.get_stage()

    def bind_to_existing_context(self, context_name: str) -> bool:
        """이미 ``open_stage`` 된 이름 있는 컨텍스트(분할 보조 타일)에 바인딩."""
        cn = str(context_name or "").strip()
        if not cn:
            return False
        self._context_name = cn
        self._anonymous = False
        return self.get_stage() is not None

    def set_root_layer_edit_target(self) -> bool:
        """REQ-005 P-3 — author 는 항상 root layer 로 향한다."""
        stage = self.get_stage()
        if stage is None:
            return False
        try:
            from pxr import Usd  # type: ignore  # noqa: F401

            stage.SetEditTarget(Usd.EditTarget(stage.GetRootLayer()))
            return True
        except Exception as exc:
            print(f"{_PRINT_PREFIX} set_root_layer_edit_target failed: {exc}", flush=True)
            return False

    # ------------------------------------------------------------------ FPS 30 고정

    def force_fixed_fps_30(self) -> None:
        """Master stage / omni.timeline / carb.settings 3중으로 LAM_FIXED_FPS(=30) 강제.

        사용자 요구(2026-05-11): "하단 타임라인 창의 fps 가 60 으로 되어있어. 그 부분도
        전부 기본 설정이 30 으로 되어야 해."

        2026-05-13 — FBX→USD 자산 (실무 데이터) 로드 시 1000 프레임이 800 부근에서
        평가되는 회귀 분석 결과 — master tcps 는 30 으로 박혀도 ``omni.timeline`` 의
        실제 framerate 가 24/60 으로 작동하면 timeline slider frame ↔ stage timecode
        매핑이 ``ratio = stage_tcps / timeline_fps`` 만큼 어긋난다. 일부 Kit 빌드에서는
        ``set_target_framerate`` setter 가 silently fail 하므로 ``carb.settings`` 의
        timeline 관련 키도 함께 박아 정합성을 보장한다.

        본 메서드는 다음을 모두 시도한다(가용한 것만, 실패는 진단 로그만 남김):
        - `stage.SetTimeCodesPerSecond(30)`  (timeCode 평가 단위)
        - `stage.SetFramesPerSecond(30)`     (UI / FCurve 표시 단위)
        - `timeline.set_target_framerate(30)` / `set_time_codes_per_second(30)`
        - ``carb.settings`` 의 timeline framerate 관련 키 best-effort set
        - 마지막에 ``timeline.get_time_codes_per_seconds()`` 등으로 실제 적용값을
          read-back 해서 진단 로그로 출력 — 사용자가 한 줄로 확인 가능.

        호출 시점: `ensure_context` / `open_master` 직후, evaluator 시작 시,
        그리고 ``add_usd`` 직후 (외부 요인으로 framerate 가 다시 변경되어도 환원).
        """
        try:
            from .lam_types import LAM_FIXED_FPS
        except Exception:
            LAM_FIXED_FPS = 30.0  # type: ignore[assignment]

        target = float(LAM_FIXED_FPS)
        stage = self.get_stage()
        if stage is not None:
            try:
                cur_tcps = float(stage.GetTimeCodesPerSecond())
            except Exception:
                cur_tcps = -1.0
            try:
                cur_fps = float(stage.GetFramesPerSecond())
            except Exception:
                cur_fps = -1.0
            try:
                stage.SetTimeCodesPerSecond(target)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} master SetTimeCodesPerSecond({target}) FAIL: {exc}",
                    flush=True,
                )
            try:
                stage.SetFramesPerSecond(target)
            except Exception as exc:
                print(
                    f"{_PRINT_PREFIX} master SetFramesPerSecond({target}) FAIL: {exc}",
                    flush=True,
                )
            if abs(cur_tcps - target) > 1e-6 or abs(cur_fps - target) > 1e-6:
                print(
                    f"{_PRINT_PREFIX} master fps/tcps ({cur_fps}, {cur_tcps}) → "
                    f"({target}, {target}) (forced)",
                    flush=True,
                )

        ti = None
        try:
            import omni.timeline as _ot  # type: ignore

            ti = _ot.get_timeline_interface()
        except Exception:
            ti = None
        if ti is not None:
            for setter_name in (
                "set_target_framerate",
                "set_time_codes_per_seconds",
                "set_time_codes_per_second",
                "set_ticks_per_second",
            ):
                fn = getattr(ti, setter_name, None)
                if fn is None:
                    continue
                try:
                    fn(target)
                except Exception as exc:
                    print(
                        f"{_PRINT_PREFIX} timeline {setter_name}({target}) "
                        f"FAIL: {exc}",
                        flush=True,
                    )

        # carb.settings best-effort — 일부 Kit 빌드에서 timeline framerate 가 setter API
        # 가 아닌 settings 키로만 갱신되는 경우가 있다. 키 이름이 빌드마다 달라 후보를
        # 여러 개 시도하고 각 결과는 진단으로만 남긴다.
        try:
            import carb.settings  # type: ignore

            settings = carb.settings.get_settings()
        except Exception:
            settings = None
        if settings is not None:
            for key in (
                "/app/window/timeline/timeCodesPerSecond",
                "/app/window/timeline/framesPerSecond",
                "/app/window/timeline/playFramerate",
                "/persistent/app/usd/timeCodesPerSecond",
                "/persistent/app/usd/framesPerSecond",
                "/persistent/app/window/timeline/timeCodesPerSecond",
                "/persistent/app/window/timeline/framesPerSecond",
            ):
                try:
                    settings.set(key, float(target))
                except Exception:
                    continue

        # Read-back — 적용 결과를 한 줄로 출력해 사용자가 즉시 확인 가능.
        read_tl: Dict[str, Any] = {}
        if ti is not None:
            for name in (
                "get_time_codes_per_seconds",
                "get_target_framerate",
                "get_ticks_per_second",
            ):
                g = getattr(ti, name, None)
                if callable(g):
                    try:
                        read_tl[name] = float(g())
                    except Exception:
                        read_tl[name] = "EXC"
        read_st = "?"
        read_fs = "?"
        if stage is not None:
            try:
                read_st = f"{float(stage.GetTimeCodesPerSecond()):.3f}"
            except Exception:
                pass
            try:
                read_fs = f"{float(stage.GetFramesPerSecond()):.3f}"
            except Exception:
                pass
        print(
            f"{_PRINT_PREFIX} fps_sync target={target} stage(tcps={read_st}, "
            f"fps={read_fs}) timeline={read_tl}",
            flush=True,
        )

    def make_relative_to_master(self, abs_path: str) -> str:
        """REQ-005 P-2 — master.usd 가 저장된 경우 상대 경로 변환을 시도. 실패 시 절대 경로 그대로."""
        if not abs_path:
            return abs_path
        if self._anonymous or not self._master_path:
            return abs_path
        try:
            base = os.path.dirname(os.path.abspath(self._master_path))
            return os.path.relpath(os.path.abspath(abs_path), start=base).replace("\\", "/")
        except Exception:
            return abs_path

    # -------------------------------------------- per-instance sublayer (핫픽스 7)

    def _pick_attach_layer(self, stage):
        """인스턴스 sublayer 를 어느 layer 의 sublayer 로 등록할지 선택.

        핫픽스 8 — USD layer 강도 규칙은 `Session > Root > Root.subLayers` 이므로,
        root 의 subLayerPaths 에 끼우면 root 자체보다 weaker 가 되어 master USD 의
        references 가 winner 가 된다(핫픽스 7 의 한계). 따라서 **session layer 의
        subLayerPaths 에 끼워야** stage 평가에서 strongest 그룹이 된다.

        반환: (layer, source_tag) — source_tag 는 진단 로그에 'session'/'root' 표기용.
        """
        try:
            session = stage.GetSessionLayer()
        except Exception:
            session = None
        if session is not None:
            return session, "session"
        # 보호망 — 일부 헤드리스/테스트 환경은 session layer 가 없을 수 있다.
        return stage.GetRootLayer(), "root"

    def ensure_inst_sublayer(self, prim_path: str, *, tag_hint: str = ""):
        """인스턴스 전용 anonymous Sdf.Layer 1개를 보장하고 stage 의 strongest 위치에 삽입.

        핫픽스 8 — session layer 의 subLayerPaths.insert(0, ...) 로 등록한다.
        - session layer 자체가 root layer 보다 stronger 이므로, session 의 sublayer 도
          root layer (사용자 master USD) 보다 stronger.
        - 따라서 우리가 author 한 reference 가 master USD 의 reference 를 USD ListOp
          explicit override 규칙으로 무조건 winner.
        - session layer 는 stage 와 함께 자동 폐기되어 사용자 파일에 영향 0.
        """
        stage = self.get_stage()
        if stage is None:
            return None

        try:
            from pxr import Sdf  # type: ignore
        except Exception as exc:
            print(f"{_PRINT_PREFIX} ensure_inst_sublayer: pxr.Sdf unavailable: {exc}", flush=True)
            return None

        layer = self._inst_sublayers.get(prim_path)
        if layer is not None:
            return layer

        tag = _slug_for_tag(tag_hint or prim_path)
        try:
            layer = Sdf.Layer.CreateAnonymous(f"lam_inst_{tag}")
        except Exception as exc:
            print(f"{_PRINT_PREFIX} CreateAnonymous failed for {prim_path}: {exc}", flush=True)
            return None
        self._inst_sublayers[prim_path] = layer

        try:
            attach_layer, src = self._pick_attach_layer(stage)
            sub_paths = list(attach_layer.subLayerPaths)
            if layer.identifier not in sub_paths:
                sub_paths.insert(0, layer.identifier)
                attach_layer.subLayerPaths = sub_paths
            try:
                attach_id = attach_layer.identifier
            except Exception:
                attach_id = "?"
            print(
                f"{_PRINT_PREFIX} sublayer attached prim={prim_path} "
                f"layer={layer.identifier} into={src}({attach_id}) "
                f"subLayerPaths_count={len(sub_paths)}",
                flush=True,
            )
        except Exception as exc:
            print(f"{_PRINT_PREFIX} attach sublayer failed prim={prim_path}: {exc}", flush=True)
            try:
                self._inst_sublayers.pop(prim_path, None)
            except Exception:
                pass
            return None

        return layer

    def get_inst_sublayer(self, prim_path: str):
        """이미 만들어진 인스턴스 sublayer 반환. 없으면 None."""
        return self._inst_sublayers.get(prim_path)

    def remove_inst_sublayer(self, prim_path: str) -> bool:
        """인스턴스 sublayer 1개를 attach 한 layer (session 우선) 에서 떼어내고 폐기."""
        layer = self._inst_sublayers.pop(prim_path, None)
        if layer is None:
            return False
        stage = self.get_stage()
        if stage is None:
            return True  # in-memory layer 만 GC 에 맡김.
        try:
            ident = layer.identifier
        except Exception:
            ident = ""
        # 핫픽스 8 — session 우선이지만 과거 root 에 attach 된 경우도 정리(둘 다 시도).
        for cand in [stage.GetSessionLayer(), stage.GetRootLayer()]:
            try:
                if cand is None:
                    continue
                sub_paths = list(cand.subLayerPaths)
                if ident and ident in sub_paths:
                    sub_paths.remove(ident)
                    cand.subLayerPaths = sub_paths
            except Exception:
                pass
        try:
            print(f"{_PRINT_PREFIX} sublayer detached prim={prim_path} layer={ident}", flush=True)
        except Exception:
            pass
        return True

    def clear_all_inst_sublayers(self) -> None:
        """모든 인스턴스 sublayer 청소(open_master / new_stage 시점 등)."""
        prim_paths = list(self._inst_sublayers.keys())
        for p in prim_paths:
            try:
                self.remove_inst_sublayer(p)
            except Exception:
                pass
        self._inst_sublayers.clear()


__all__ = ["MasterStage", "LAM_MASTER_CONTEXT_NAME"]

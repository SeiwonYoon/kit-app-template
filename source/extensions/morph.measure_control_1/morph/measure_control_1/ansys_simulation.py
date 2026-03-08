# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
[이 파일이 하는 일]
ANSYS(엔시스)라는 "구조 해석 프로그램"을 파이썬에서 부르는 역할을 합니다.
오브젝트에 주어진 압력·온도를 넣으면, ANSYS가 "이만큼 휘어질 거예요"라고 계산해 주고,
그 결과를 3D 화면에서 오브젝트가 휘어 보이도록 바꿔 줍니다.

[PyAnsys란?]
PyAnsys = 파이썬으로 ANSYS를 조종하는 도구.
보통 ANSYS는 창을 띄워서 사람이 버튼을 눌러 쓰는데,
PyAnsys를 쓰면 파이썬 코드로 "도형 만들어", "재질 넣어", "힘 가해", "계산해"라고 시키는 것과 같습니다.

[전체 흐름 (쉽게)]
1. 우리 프로그램(Omniverse)에 "압력 150, 온도 20" 같은 값이 있어요.
2. 이 값을 ANSYS한테 넘겨요 → "이 조건으로 구조 해석 해줘."
3. ANSYS가 블록 하나 만들어서, 재질 넣고, 150만큼 힘을 주고, 계산해요.
4. "Y방향으로 이만큼 움직였어요" 하는 숫자(변위)를 받아요.
5. 그 숫자를 3D 오브젝트의 "크기(스케일)"에 반영해서, 휘어 보이게 해요.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

# TYPE_CHECKING: 타입 힌트만 쓸 때 쓰는 import (실행 시에는 안 불러옴)
if TYPE_CHECKING:
    from pxr import Gf, Usd, UsdGeom


# [이 숫자의 의미]
# ANSYS가 준 "변위(미터)"를 3D 오브젝트의 "몇 배로 키우고 줄일지" 바꿀 때 쓰는 비율이에요.
# 100이면 변위 0.01m → 스케일에 1.0 만큼 반영돼서, 화면에서 휨이 잘 보여요.
DEFORMATION_TO_SCALE_FACTOR = 100.0


class AnsysSimulationManager:
    """
    [이 클래스가 하는 일]
    ANSYS 프로그램을 "한 번만" 켜 두고, 필요할 때마다 "압력/온도 넣어서 해석해 줘"라고 부르는
    관리자 같은 클래스예요. 켜는 건 한 번, 부르는 건 여러 번 가능해요.
    """

    def __init__(self) -> None:
        """처음 만들 때는 아직 ANSYS를 켜지 않아요. 나중에 initialize_solver()에서 한 번만 켜요."""
        self._mapdl = None   # 나중에 여기에 "ANSYS와 대화하는 통로"가 들어가요.
        self._available = False  # ANSYS가 제대로 켜졌는지 여부

    def initialize_solver(self) -> bool:
        """
        [이 함수가 하는 일]
        ANSYS(MAPDL) 프로그램을 "한 번만" 실행해서 켜 두는 함수예요.
        확장이 시작될 때 딱 한 번만 불러야 하고, 여러 번 부르면 안 돼요(느려지고 꼬일 수 있어요).

        [PyAnsys가 어떻게 쓰이나요]
        - launch_mapdl() = "ANSYS 프로그램 켜 줘"라고 부르는 함수예요.
        - mode="grpc" = 창 없이 뒤에서 조용히 돌리라는 뜻이에요.
        - start_timeout=60 = 60초 안에 안 켜지면 포기해요.

        Returns:
            True = 잘 켜졌어요. False = PyAnsys가 없거나, ANSYS가 안 켜졌어요.
        """
        # 이미 한 번 켰으면 다시 안 켜요 (한 번만 켜야 하니까)
        if self._mapdl is not None:
            return self._available
        try:
            # psutil 5.x에는 Process.net_connections 없고 connections만 있음. ansys-mapdl-core는 net_connections를 씀.
            # net_connections가 없으면 connections를 그대로 쓰도록 패치해서 버전 차이 무시.
            try:
                import psutil
                if not getattr(psutil.Process, "net_connections", None) and getattr(
                    psutil.Process, "connections", None
                ):
                    psutil.Process.net_connections = psutil.Process.connections
            except Exception:
                pass
            # PyAnsys에서 MAPDL 켜는 함수와 실행 파일 경로 찾기 함수를 가져와요.
            from ansys.mapdl.core import launch_mapdl
            # Kit 앱에는 터미널이 없어서 "경로 입력하세요" 프롬프트가 뜨면 멈춤. 먼저 경로만 찾고, 없으면 launch 안 함.
            exec_file = None
            try:
                from ansys.mapdl.core.launcher import get_default_ansys_path
                exec_file = get_default_ansys_path()
            except Exception:
                try:
                    from ansys.mapdl.core.launcher import get_default_ansys
                    _path_ver = get_default_ansys()
                    if _path_ver:
                        exec_file = _path_ver[0] if isinstance(_path_ver, (list, tuple)) else _path_ver
                except Exception:
                    pass
            if not exec_file:
                self._mapdl = None
                self._available = False
                print("pyansys 안켜짐: ANSYS MAPDL 실행 파일을 찾을 수 없습니다. PC에 ANSYS Mechanical APDL이 설치되어 있어야 합니다.")
                return False
            # 여기서 실제로 ANSYS를 실행해요. exec_file을 넘기면 프롬프트 없이 실행됨. grpc = 창 없이, 60초까지 기다려요.
            self._mapdl = launch_mapdl(exec_file=exec_file, mode="grpc", start_timeout=60)
            self._available = True
            print("pyansys 잘 켜짐========================")
            return True
        except Exception as e:
            # PyAnsys가 없거나, ANSYS MAPDL 실행 파일이 없거나, 라이선스 등 오류 시 여기로 옵니다.
            import traceback
            self._mapdl = None
            self._available = False
            print("pyansys 안켜짐========================")
            print(f"  사유: {e}")
            traceback.print_exc()
            return False

    def run_simulation(self, pressure: float, temperature: float) -> float:
        """
        [이 함수가 하는 일]
        "압력이 이만큼, 온도가 이만큼이에요"라고 넘기면,
        ANSYS한테 "이 조건으로 구조 해석 해줘"라고 시키고,
        "Y방향으로 가장 많이 움직인 거리(변위)"를 받아서 돌려줘요.

        [Omniverse → ANSYS]
        - pressure: 우리 3D 오브젝트의 "압력" 필드 값이에요. ANSYS한테 "이만큼 힘(FY)으로 넣어줘"라고 해요.
        - temperature: 나중에 열 해석 쓸 때 쓰려고 넣어 둔 거예요. 지금은 해석에는 안 써요.

        [PyAnsys로 하는 일 순서]
        1) clear() → 이전에 만든 모델 다 지우기
        2) prep7() → "준비 단계" 들어가기 (도형·재질·메시 넣는 단계)
        3) block() → 1x1x1 정육면체 하나 만들기
        4) mp() → 재질 정하기 (강철처럼: 탄성계수, 포아송비)
        5) et(), vmesh() → 메시(쪼개기) 해서 계산할 점들 만들기
        6) d() → Z=0인 면을 고정 (안 고정하면 막 흔들려서 계산이 안 돼요)
        7) f() → 모든 점에 Y방향으로 pressure 만큼 힘 주기
        8) solve() → "이제 계산해!" 하고 해석 돌리기
        9) post1(), nodal_displacement("Y") → "Y방향으로 얼마나 움직였어?" 결과 읽기
        10) 그 중 가장 큰 절댓값을 반환 (휨 정도를 이 숫자로 나타내요)

        Returns:
            Y방향 최대 변위(미터). 0.0 = 오류 났거나 ANSYS 못 쓸 때.
        """
        if not self._available or self._mapdl is None:
            return 0.0
        try:
            # --- 1단계: 이전 작업 지우고 "준비" 모드로 들어가기 ---
            self._mapdl.clear()   # 예전에 만든 블록·재질·메시 다 지워요.
            self._mapdl.prep7()   # PREP7 = "준비 단계" (도형 만들고, 재질 넣고, 메시 쪼개는 단계)

            # --- 2단계: 간단한 블록 하나 만들기 (가로1, 세로1, 높이1) ---
            # block(0,1, 0,1, 0,1) = X 0~1, Y 0~1, Z 0~1 구간에 정육면체 하나 만들어요.
            self._mapdl.block(0, 1, 0, 1, 0, 1)
            # 재질 1번: 탄성계수(EX) = 200e9 (강철 비슷), 포아송비(NUXY) = 0.3
            self._mapdl.mp("EX", 1, 200e9)
            self._mapdl.mp("NUXY", 1, 0.3)

            # --- 3단계: 블록을 "요소"로 쪼개기 (메시) ---
            # et(1, "SOLID185") = 1번 요소 타입을 SOLID185(입체 요소)로 써요.
            self._mapdl.et(1, "SOLID185")
            self._mapdl.vsel("ALL")    # 방금 만든 부피(블록) 전부 선택
            self._mapdl.esize(0.5)    # 한 변을 0.5 크기로 쪼개요 (거칠게 쪼개서 빨리 계산)
            self._mapdl.vmesh("ALL")  # 선택한 부피 전부 메시 쪼개기 → 노드(점)들이 생겨요

            # --- 4단계: 경계 조건 — 한쪽 면을 고정하기 ---
            # Z=0인 면의 노드들을 선택해서, 그 점들을 "움직이지 마"라고 고정해요.
            # 안 하면 블록이 통째로 날아가서 해석이 안 돼요.
            self._mapdl.nsel("S", "LOC", "Z", 0)  # Z위치가 0인 노드만 선택
            self._mapdl.d("ALL", "ALL", 0)       # 그 노드들 X,Y,Z 이동 전부 0으로 고정
            self._mapdl.allsel()                 # 다시 "전체 선택" 상태로

            # --- 5단계: 힘 넣기 (Omniverse의 "압력" 값을 여기서 FY로 넣어요) ---
            # f("ALL", "FY", pressure) = "선택된 모든 노드에 Y방향으로 pressure 만큼 힘을 줘."
            self._mapdl.f("ALL", "FY", pressure)

            # --- 6단계: 해석 실행 (솔버 돌리기) ---
            self._mapdl.run("/SOLU")   # /SOLU = 해석 단계로 들어가요
            self._mapdl.antype("STATIC")  # 정적 해석 (움직이는 건 아니고, 힘 받고 찌그러진 모양만 구함)
            self._mapdl.solve()        # "이제 계산해!" → ANSYS가 노드별 변위를 계산해요
            self._mapdl.finish()       # 해석 단계 끝내기

            # --- 7단계: 결과 읽기 (후처리) ---
            # post1 = "결과 보는 모드"로 들어가요.
            self._mapdl.post1()
            # set(1, 1) = 1번 하중 step, 1번 sub step 결과를 불러와요.
            self._mapdl.set(1, 1)
            # nodal_displacement("Y") = "모든 노드의 Y방향 변위"를 배열로 받아요 (미터 단위).
            disp_y = self._mapdl.post_processing.nodal_displacement("Y")
            if disp_y is None or len(disp_y) == 0:
                return 0.0
            # 그 중 "절댓값이 가장 큰 것" = 가장 많이 움직인 정도 → 이걸 휨 크기로 써요.
            import numpy as np
            return float(np.abs(np.asarray(disp_y)).max())
        except Exception:
            return 0.0

    def apply_result_to_prim(
        self,
        prim: "Usd.Prim",
        deformation: float,
        base_scale: "Gf.Vec3d",
    ) -> None:
        """
        [이 함수가 하는 일]
        ANSYS가 준 "변위(deformation)" 숫자를, 3D 오브젝트(prim)의 "크기(스케일)"에 반영해요.
        변위가 크면 Y방향은 줄이고, X방향은 살짝 키워서 "휘어 보이게" 만드는 거예요.

        [결과를 prim에 어떻게 넣나요]
        - deformation = run_simulation()이 준 "Y방향 최대 변위(미터)"예요.
        - 이걸 비율 k로 바꿔서: scale_x는 키우고(1+k), scale_y는 줄이고(1-k), scale_z는 그대로.
        - prim의 Scale xformOp(크기 값)을 이렇게 바꿔 주면, 뷰포트에서 오브젝트가 휘어 보여요.
        """
        from pxr import Gf, UsdGeom
        if prim is None or not prim.IsValid():
            return
        # prim이 "위치·크기 바꿀 수 있는" 타입인지 확인하고, Xformable로 감싸요.
        xform = UsdGeom.Xformable(prim)
        if not xform:
            return
        # 변위(미터)를 "스케일에 곱할 비율"로 바꿔요. 숫자가 너무 작으니까 DEFORMATION_TO_SCALE_FACTOR를 곱해요.
        k = DEFORMATION_TO_SCALE_FACTOR * deformation
        # 휨처럼 보이게: X는 살짝 키우고, Y는 줄이고, Z는 그대로.
        scale_x = base_scale[0] * (1.0 + k)
        scale_y = base_scale[1] * (1.0 - k)
        scale_z = base_scale[2]
        scale = Gf.Vec3d(scale_x, scale_y, scale_z)

        # prim에 이미 "Scale" 연산이 있는지 찾아요.
        scale_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                scale_op = op
                break
        if not scale_op:
            scale_op = xform.AddScaleOp()  # 없으면 하나 추가해요.
        # 계산한 scale 값을 prim에 넣어요 → 화면에서 오브젝트가 휘어 보여요.
        scale_op.Set(scale)

    def shutdown(self) -> None:
        """
        [이 함수가 하는 일]
        확장이 끝날 때 ANSYS 프로그램을 끄고, 통로(_mapdl)를 정리해요.
        extension의 on_shutdown()에서 한 번 불러요.
        """
        if self._mapdl is not None:
            try:
                self._mapdl.exit()  # MAPDL한테 "종료해"라고 보내요.
            except Exception:
                pass
            self._mapdl = None
        self._available = False

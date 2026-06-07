** 1번째 프롬프트

나는 현재 NVIDIA Omniverse Kit App 기반 환경에서
여러 개의 FBX → USD 변환 애니메이션을
하나의 Stage 안에서 독립적으로 재생 제어하는 시스템을 구현하고 싶다.

중요한 점은:

USD Stage Timeline 은 하나만 존재하기 때문에,
기본 timeline 기반 재생으로는
여러 애니메이션을 서로 다른 시간축으로 독립 제어하기 어렵다.

내가 원하는 동작은 다음과 같다:

예시:
- A.fbx 애니메이션:
  0~100 프레임
- B.fbx 애니메이션:
  50~150 프레임

상황:
- A 애니메이션이 재생 중이며 현재 50프레임까지 진행됨
- 그 순간 B 애니메이션을 시작
- 이후:
  - A는 계속 자신의 프레임 진행
  - B도 자신의 프레임 진행
  - 둘은 동시에 독립적으로 재생되어야 함

즉,
"Stage 전체 current time" 을 공유하는 구조가 아니라,
각 애니메이션/캐릭터마다 독립적인 local animation time 을 가져야 한다.

내가 원하는 것은:
- 여러 USD skeletal animation clip 을 동시에 독립 재생
- 각 animation 별:
  - play
  - pause
  - stop
  - seek(frame jump)
  - speed control
  - loop
  - reverse(optional)
  를 지원
- 서로 다른 시작 프레임/구간 재생 가능
- 서로 timeline 충돌 없이 동작
- stage timeline 과 최대한 분리된 구조

그리고 중요한 점:
나는 Animation Graph(state machine) 자체보다,
실제로는 "animation clip sampling runtime" 구조가 필요한 상황이다.

따라서 아래 내용을 자세히 설명해줘:

1.
왜 USD 기본 Timeline 구조로는
독립 animation playback 이 어려운지

2.
Omniverse Kit / USD 에서
Skeletal Animation 이 내부적으로 어떻게 동작하는지

3.
UsdSkel.Animation 과 Skeleton 관계

4.
Animation Graph 와
runtime animation sampling 의 차이점

5.
내 요구사항에 가장 적합한 아키텍처

6.
추천하는 구조:
- USD = animation asset storage
- Python runtime = playback controller
- optional OmniGraph realtime update

7.
각 캐릭터/애니메이션마다
독립 local animation clock 를 가지는 방식 설명

예:
class AnimationController:
    current_frame
    speed
    playing
    loop
    start_frame
    end_frame

8.
매 frame update 시:
- local time 계산
- animation sampling
- skeleton pose 적용
하는 전체 흐름 설명

9.
Omniverse Kit 에서 실제 구현 시:
- 어떤 API 를 써야 하는지
- UsdSkel 사용 방법
- skeleton pose evaluation 방식
- timeline 없이 animation sampling 가능한지
- performance 고려사항
- multiple character runtime 관리 방법

10.
가능하면:
- Python 기반 pseudo-code
- update loop 구조
- independent animation player 구조
- animation manager 구조
까지 예시 포함해서 설명해줘.

나는 현재:
- Omniverse Kit App Template 기반
- Python 사용 중
- 여러 FBX 를 USD 로 변환 후 stage 에 로드한 상태
- 각 애니메이션을 timeline 기반이 아닌
  독립 runtime 방식으로 제어하고 싶은 상황이다.

실제 엔진 구조 관점에서 설명해줘.

** 2번째 프롬프트

현재 나는 NVIDIA Omniverse Kit App 기반 프로젝트에서
여러 개의 FBX → USD 변환 애니메이션 자산을
하나의 Stage 안에서 동시에 독립적으로 제어하는 시스템을 구현 중이다.

현재 구조와 문제를 정확히 설명하겠다.

# 현재 구조

- 여러 개의 FBX 파일 존재
- 각 FBX 내부에는 이미 제작된 복잡한 animation 포함
    - skeletal animation
    - joint animation
    - hierarchy animation
    - keyframe animation
- 각 FBX를 USD로 변환
- 하나의 Stage에 여러 USD를 reference 로드

예시:

/World
    /Machine_A
    /Machine_B
    /Machine_C

각 Machine 은:
- 자체 skeleton
- 자체 animation clip
- 자체 keyframe animation
을 포함하고 있다.

# 현재 문제

현재는 다음 방식으로 재생 중이다:

stage.SetCurrentTimeCode(frame)

그런데 USD의 evaluation 모델 특성상:

- Stage 전체가 단일 current time 을 공유
- 모든 time-sampled attribute 가 같은 timeCode 로 평가됨
- 모든 animation 이 동시에 동일 frame 기준으로 평가됨

즉:

stage.SetCurrentTimeCode(50)

호출 시:
- Machine_A = frame 50
- Machine_B = frame 50
- Machine_C = frame 50

동시에 평가된다.

하지만 내가 원하는 것은:

- 각 USD animation 이 독립적인 local animation time 을 가져야 함
- 서로 다른 frame 을 동시에 유지 가능해야 함

예:

Machine_A → frame 50
Machine_B → frame 120
Machine_C → pause 상태

동시에 viewport 에 보여야 한다.

또는:

- A 가 움직이는 도중
- B 가 중간에 시작
- C 는 정지
- D 는 다른 속도

등의 독립 runtime playback 이 필요하다.

# 매우 중요한 점

이 시스템은:
- 단순 transform animation 이 아님
- 이미 FBX 내부에 제작된 animation clip 을 반드시 사용해야 함
- animation 데이터를 새로 만드는 것이 아님
- 기존 FBX/USD animation 데이터를 runtime 에서 활용해야 함

그리고 중요한 조사 결과:

USD Stage evaluation 구조상:

stage.SetCurrentTimeCode()

기반으로는:
- independent animation playback 이 불가능함
- USD value resolution model 자체와 충돌함

이 점은 이미 확인되었다.

# 따라서 원하는 최종 방향

USD global timeline playback 을 사용하지 않고,

각 animation clip 을 runtime 에서 독립적으로 sampling 하는 구조로 변경하고 싶다.

즉:

❌ 기존 방식:
stage.SetCurrentTimeCode()

✅ 목표 방식:
sample(animation_A, local_time_A)
sample(animation_B, local_time_B)

그리고 계산된 pose 를
각 skeleton/joint 에 직접 적용하는 구조를 원한다.

# Cursor 에서 수행해야 할 작업

현재 프로젝트 구조를 자동 분석하고,
기존 timeline 기반 구조를
independent runtime animation sampling 구조로 변경해줘.

특히 아래 항목을 중점적으로 분석 및 수정해줘.

--------------------------------------------------
[1] 현재 구조 자동 분석
--------------------------------------------------

프로젝트 전체를 분석해서:

- stage.SetCurrentTimeCode 사용 위치
- timeline 기반 playback 코드
- omni.timeline 사용 부분
- animation playback 구조
- FBX/USD 로드 구조
- skeleton 구조
- UsdSkel 사용 여부
- animation graph 사용 여부

를 전부 찾아줘.

그리고:
- 어떤 부분이 USD global timeline evaluation 에 의존하는지
- independent playback 을 방해하는 구조가 무엇인지
분석 보고서를 작성해줘.

--------------------------------------------------
[2] 기존 timeline playback 제거
--------------------------------------------------

아래 패턴들을 모두 찾아서 제거 또는 비활성화:

- stage.SetCurrentTimeCode(...)
- omni.timeline timeline control
- global frame playback
- stage current time sync 구조

그리고:
"global stage time 기반 playback"
에서
"local runtime sampling 기반 playback"
으로 구조를 변경해줘.

--------------------------------------------------
[3] Runtime Animation Controller 시스템 생성
--------------------------------------------------

아래 구조를 생성해줘.

class AnimationController:
    animation_prim
    skeleton_prim

    current_time
    start_time
    end_time

    speed
    playing
    loop

    paused

각 USD animation 마다:
독립 AnimationController 인스턴스를 생성하도록 해줘.

예:

controllers = {
    "Machine_A": controllerA,
    "Machine_B": controllerB
}

--------------------------------------------------
[4] Independent Local Time 구조 구현
--------------------------------------------------

각 controller 가:

- 자기 local time 유지
- 독립 speed
- 독립 pause
- 독립 seek
- 독립 loop

가능하도록 구현해줘.

예:

controllerA.current_time = 50
controllerB.current_time = 120

동시에 유지 가능해야 함.

--------------------------------------------------
[5] Runtime Update Loop 구현
--------------------------------------------------

매 frame:

for controller in controllers:
    controller.update(dt)

형태로 동작하도록 구현.

update 내부에서는:

- local time 증가
- animation sample 계산
- skeleton pose 적용

수행.

--------------------------------------------------
[6] UsdSkel 기반 Animation Sampling 구현
--------------------------------------------------

UsdSkel API 를 사용해서:

- animation clip 에서 특정 시간의 pose sampling
- joint transform 계산
- skeleton 적용

구현.

중요:
USD global stage evaluation 을 사용하지 말고,
각 animation 을 독립 시간으로 직접 평가하는 구조로 구현.

가능한 경우:
- UsdSkel.Animation
- UsdSkel.Skeleton
- animation query API
- skel cache
- compute joint transforms

등을 활용해줘.

--------------------------------------------------
[7] Skeleton Pose 직접 적용
--------------------------------------------------

샘플링 결과를:

- joint transform
- local transform
- skeleton pose

에 직접 적용하는 runtime 구조 구현.

중요:
Stage current time 을 변경하지 말 것.

--------------------------------------------------
[8] Multi Animation Runtime 지원
--------------------------------------------------

동시에 여러 animation 이 독립 재생되도록 구현.

예:

A → frame 50
B → frame 120
C → pause

동시 표시 가능해야 함.

--------------------------------------------------
[9] 디버깅 및 검증 기능 추가
--------------------------------------------------

아래 기능 추가:

- 각 controller current time 출력
- 현재 animation 상태 출력
- runtime sampling 성공 여부 로그
- stage current time 과 분리되었는지 검증

그리고:
실제로 서로 다른 frame 상태가
동시에 viewport 에 표시되는지 확인하는 테스트 코드 추가.

--------------------------------------------------
[10] 성능 고려
--------------------------------------------------

아래 고려사항 포함:

- SkelCache 재사용
- unnecessary recompute 방지
- update frequency 관리
- 다수 animation 동시 처리 최적화

--------------------------------------------------
[11] 최종 목표
--------------------------------------------------

최종적으로:

하나의 viewport 안에서:
- 여러 USD 장비들이
- 각자 독립 animation frame 을 유지하며
- 서로 다른 시점의 animation 을
- 동시에 자연스럽게 재생

가능한 구조를 구현하고 싶다.

중요:
"USD global timeline playback"
이 아니라,

"custom runtime local-time animation playback"

구조로 변경하는 것이 핵심이다.

--------------------------------------------------
[12] 추가 요구사항
--------------------------------------------------

가능하면:

- 실제 수정 파일 목록
- 변경 전/후 구조
- runtime architecture diagram
- update loop 흐름
- animation sampling 흐름

까지 정리해줘.

그리고:
현재 프로젝트 구조를 기반으로
자동 수정 가능한 부분은 직접 수정하고,
불가능한 부분은 TODO 주석과 함께 설명해줘.


** 2번째 프롬프트


현재 NVIDIA Omniverse Kit App 기반 프로젝트에서
여러 개의 FBX → USD 변환 애니메이션 자산을
하나의 Stage 안에서 독립적으로 제어하는 시스템을 구현 중이다.

중요:
현재 목표는 Animation Graph 사용이 아니라,
USD 내부의 skeletal animation 데이터를
runtime 에서 직접 independent playback 하는 구조로 변경하는 것이다.

--------------------------------------------------
[현재 상황]
--------------------------------------------------

현재 구조:

- 여러 FBX 파일 존재
- 각 FBX 내부에는 이미 제작된 복잡한 animation 포함
    - skeletal animation
    - joint animation
    - hierarchy animation
    - keyframe animation
- FBX → USD 변환 완료
- 여러 USD를 하나의 Stage에 reference 로드

예시:

/World
    /Machine_A
    /Machine_B
    /Machine_C

각 Machine 은:
- 자체 Skeleton
- 자체 animation clip
- 자체 keyframe animation
을 포함한다.

--------------------------------------------------
[현재 문제]
--------------------------------------------------

현재는 다음 구조로 playback 중:

stage.SetCurrentTimeCode(frame)

하지만 USD evaluation model 특성상:

- Stage 는 단일 current time 을 공유
- 모든 time-sampled attribute 가 같은 frame 으로 평가됨
- 모든 referenced USD animation 이 동시에 동일 frame 기준으로 평가됨

즉:

stage.SetCurrentTimeCode(50)

호출 시:
- Machine_A → frame 50
- Machine_B → frame 50
- Machine_C → frame 50

동시에 평가된다.

하지만 내가 원하는 것은:

Machine_A → frame 50
Machine_B → frame 120
Machine_C → pause

상태를 동시에 viewport 에 표시하는 것이다.

즉:
각 animation 이 독립 local time 을 가져야 한다.

--------------------------------------------------
[매우 중요한 이해]
--------------------------------------------------

나는 더 이상:
"USD 파일 자체를 playback"
하려는 것이 아니다.

대신:

"UsdSkel.Animation + Skeleton pair"

를 독립 runtime sampling 하는 구조로 변경하려고 한다.

즉:

❌ 기존:
USD file playback

✅ 목표:
Animation prim runtime evaluation

--------------------------------------------------
[중요한 구현 방향]
--------------------------------------------------

현재까지 조사 결과:

❌ stage.SetCurrentTimeCode 기반 independent playback 불가능

왜냐면:
USD value resolution model 상:
- Stage current time 은 하나
- 모든 animation 은 동일 current time 기준 평가

따라서:

❌ global timeline playback 제거 필요

그리고 다음 구조로 변경해야 한다:

✅ animation clip 직접 sampling
✅ local animation time per controller
✅ skeleton pose 직접 적용

--------------------------------------------------
[Cursor 가 수행해야 할 핵심 작업]
--------------------------------------------------

현재 프로젝트를 분석하고:

"USD file playback"
중심 구조에서

"UsdSkel.Animation runtime sampling"
구조로 변경해줘.

--------------------------------------------------
[1] 현재 Stage 안의 Animation 구조 자동 분석
--------------------------------------------------

프로젝트 실행 후:
현재 Stage 내부를 자동 탐색해서 아래 정보를 출력해줘.

- 모든 UsdSkel.Animation prim
- 모든 UsdSkel.Skeleton prim
- 모든 SkelRoot
- animation binding 관계
- 어떤 skeleton 이 어떤 animation 을 사용하는지

중요:
curve animation 존재 여부로 찾지 말 것.

FBX importer 특성상:
- animation curve 가 직접 보이지 않을 수 있음
- baked skeletal animation 형태일 가능성 높음

따라서:
반드시 UsdSkel 기반으로 탐색할 것.

--------------------------------------------------
[2] Animation Prim 탐색 코드 구현
--------------------------------------------------

반드시 아래 개념 기반으로 구현:

from pxr import UsdSkel

for prim in stage.Traverse():
    if prim.IsA(UsdSkel.Animation):
        ...

Stage 내부의 모든 animation prim 출력.

예상 출력:

/World/Machine_A/Animations/Take01
/World/Machine_B/Animations/Move

--------------------------------------------------
[3] Skeleton 탐색 코드 구현
--------------------------------------------------

반드시 아래 개념 기반으로 구현:

if prim.IsA(UsdSkel.Skeleton):
    ...

모든 skeleton 출력.

--------------------------------------------------
[4] Animation ↔ Skeleton Binding 관계 분석
--------------------------------------------------

UsdSkel.BindingAPI 를 사용해서:

- 어떤 animation 이
- 어떤 skeleton 과 연결되는지

자동 분석해줘.

반드시:
GetAnimationSourceRel()
사용 여부 확인.

최종적으로 아래 구조 출력:

Machine_A
 ├─ Skeleton: /World/A/Skeleton
 └─ Animation: /World/A/Animations/Take01

Machine_B
 ├─ Skeleton: /World/B/Skeleton
 └─ Animation: /World/B/Animations/Move

--------------------------------------------------
[5] 매우 중요한 구조 변경
--------------------------------------------------

현재 프로젝트는:
"USD 파일 단위 playback"
구조로 되어 있을 가능성이 높다.

이것을 반드시:

"Animation Prim + Skeleton Pair"

중심 구조로 변경해줘.

즉:

❌ 기존:
USD file playback

✅ 변경:
AnimationController(
    skeleton,
    animation
)

--------------------------------------------------
[6] Runtime AnimationController 생성
--------------------------------------------------

아래 구조 생성:

class AnimationController:

    animation_prim
    skeleton_prim

    current_time
    start_time
    end_time

    speed
    playing
    paused
    loop

각 animation pair 마다:
독립 controller 생성.

예:

controllers = {
    "Machine_A": controllerA,
    "Machine_B": controllerB
}

--------------------------------------------------
[7] Independent Local Time 구조 구현
--------------------------------------------------

각 controller 가:
독립 current_time 을 가져야 함.

예:

controllerA.current_time = 50
controllerB.current_time = 120

동시에 유지 가능해야 함.

중요:
절대 stage.SetCurrentTimeCode 사용하지 말 것.

--------------------------------------------------
[8] Runtime Sampling 구조 구현
--------------------------------------------------

각 controller.update(dt) 내부에서:

- local current_time 증가
- animation sampling
- skeleton pose 계산
- skeleton pose 적용

수행.

중요:
USD global stage evaluation 사용 금지.

반드시:
animation clip 을 직접 sampling 하는 구조로 구현.

--------------------------------------------------
[9] UsdSkel API 조사 및 활용
--------------------------------------------------

아래 API 중심으로 조사 및 구현:

- UsdSkel.Animation
- UsdSkel.Skeleton
- UsdSkel.BindingAPI
- UsdSkel.Cache
- UsdSkelQuery
- animation query
- compute joint transforms
- skeleton pose evaluation

가능한 경우:
SkelCache 재사용 구조 구현.

--------------------------------------------------
[10] Skeleton Pose 직접 적용
--------------------------------------------------

Animation sampling 결과를:

- joint transforms
- local transforms
- skeleton pose

에 직접 적용.

중요:
"timeline playback"
이 아니라
"runtime pose application"
구조여야 함.

--------------------------------------------------
[11] Multi Animation Runtime 구현
--------------------------------------------------

동시에 여러 animation 이 독립 재생되도록 구현.

예:

Machine_A → frame 50
Machine_B → frame 120
Machine_C → pause

동시에 viewport 표시 가능해야 함.

--------------------------------------------------
[12] Debugging 및 Validation 추가
--------------------------------------------------

아래 기능 추가:

- 각 controller current_time 출력
- animation sampling 성공 여부
- binding 관계 로그
- skeleton pose update 로그

그리고:
실제로 서로 다른 frame 상태가
동시에 viewport 에 표시되는지 검증하는 테스트 코드 추가.

--------------------------------------------------
[13] 성능 고려
--------------------------------------------------

반드시 고려:

- SkelCache 재사용
- unnecessary recompute 방지
- update frequency 관리
- multi animation runtime 최적화

--------------------------------------------------
[14] 최종 목표]
--------------------------------------------------

최종적으로:

하나의 viewport 안에서:

- 여러 개의 FBX 기반 USD 장비들이
- 각자 독립적인 animation frame 을 유지하고
- 서로 다른 시점의 animation 을
- 동시에 재생 가능

한 runtime 구조를 구현하고 싶다.

중요:
목표는:

❌ USD global timeline playback

이 아니라

✅ custom runtime local-time animation playback

이다.

--------------------------------------------------
[15] 추가 요구사항
--------------------------------------------------

가능하면:

- 실제 수정 파일 목록
- 변경 전/후 구조
- runtime architecture diagram
- update loop 흐름
- animation sampling 흐름
- binding 구조 설명

까지 포함해서 정리해줘.

그리고:
자동 수정 가능한 부분은 직접 수정하고,
불가능한 부분은 TODO 주석과 함께 설명해줘.
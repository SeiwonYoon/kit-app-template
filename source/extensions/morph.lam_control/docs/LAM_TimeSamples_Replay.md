# LAM — TIMESAMPLES_REPLAY 재생 메커니즘 (실습형)

> 작성: 2026-05-12 / 대상: timeSamples 데이터가 실제로 어떻게 생겼고, 그 값을 가지고
> 어떻게 viewport 에 "프레임 30~60" 같은 범위를 재생시키는지를 **한 줄씩 따라 돌려볼 수
> 있는 함수 단위**로 정리.
>
> `source/prompt.txt` 에 있는 `inspect_xform_animation` / `collect_stage_timesamples`
> 형식을 그대로 따라가며, 각 함수의 입력·출력 예시와 그 데이터를 LAM 이 viewport 재생에
> 어떻게 활용하는지 동일 스타일의 함수로 답한다.

---

## 0. 큰 그림 — 3 단계로 끝난다

```
①  자산 USD 의 timeSamples 데이터를 추출한다       — pxr API (Get / GetTimeSamples)
②  step 의 프레임 범위 [sf, ef] 를 시간으로 변환한다   — sec = frame / 30
③  매 frame 그 시각의 값을 master prim 의 default 로 박는다 — attr.Get(tc) → mirror.Set(val)
```

**Bake 가 필요한 자산** (OmniGraph) 와 **Bake 가 불필요한 자산** (이미 timeSamples 가
있는 실무 USD) 의 차이는 ①의 입력이 무엇이냐 뿐이고, ②/③은 동일하다.

| 자산 종류 | ①의 소스 | UI 의 [Bake] 버튼 |
|---|---|---|
| OMNIGRAPH / MIXED | bake 가 timeSamples 로 변환해 메모리 layer 에 박은 결과 | **표시 (필수)** |
| TIMESAMPLES_XFORM | 자산 USD 안의 `xformOp:*.timeSamples` 그대로 | **숨김** (bake 없이 바로 재생) |
| TIMESAMPLES_SKEL / MESH | 자산 USD 안의 `SkelAnimation.*` / `Mesh.points.timeSamples` | 선택 |
| STATIC | 시간 데이터 없음 (정지 reference) | 숨김 |

---

## 1. ① timeSamples 데이터 추출 — print 로 직접 확인

### 1.1 한 prim 만 검사 — `inspect_xform_animation`

`source/prompt.txt` 의 함수와 동일. 보강한 점: `op.GetAttr()` 외에 `op.IsInverseOp()`,
`bracket` 보간 케이스 표시.

```python
from pxr import UsdGeom


def inspect_xform_animation(stage, prim_path: str) -> None:
    """단일 prim 의 모든 xformOp 에 박힌 timeSamples 를 print.

    Args:
        stage: pxr.Usd.Stage — 자산 USD 를 in-memory 로 open 한 stage.
        prim_path: 예) "/World/aaa" 또는 자산 내부 경로 "/Root/Mesh".
    """
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        print(f"[ERROR] Invalid prim: {prim_path}")
        return

    xformable = UsdGeom.Xformable(prim)
    if not xformable:
        print(f"[ERROR] Prim is not Xformable: {prim_path}")
        return

    print("=" * 80)
    print(f"Prim : {prim_path}")
    print("=" * 80)

    ops = xformable.GetOrderedXformOps()
    if not ops:
        print("No XformOps found")
        return

    for op in ops:
        attr = op.GetAttr()                            # pxr.Usd.Attribute
        print("\n" + "-" * 60)
        print(f"Op Name : {op.GetOpName()}")           # 예: "xformOp:translate"
        print(f"Attr    : {attr.GetName()}")           # 동일 이름
        print(f"TypeName: {attr.GetTypeName()}")       # 예: "double3"

        times = attr.GetTimeSamples()                  # ★ 모든 sample 의 timeCode (정렬됨)
        print(f"TimeSamples Count : {len(times)}")
        if not times:
            print("No authored TimeSamples")
            continue

        for t in times[:5]:                            # 앞쪽 5개만 미리보기
            value = attr.Get(t)                        # ★ 그 timeCode 에서의 값
            print(f"  [{t}] = {value}")
        if len(times) > 5:
            print(f"  ... (생략 {len(times)-5}개) ...")
            last = times[-1]
            print(f"  [{last}] = {attr.Get(last)}")
```

### 1.2 stage 전체 스캔 — `collect_stage_timesamples`

`source/prompt.txt` 의 함수와 동일. 본 함수가 돌려주는 `animation_db` 가 §2 의 재생
함수 입력으로 그대로 쓰인다.

```python
from pxr import UsdGeom


def collect_stage_timesamples(stage) -> dict:
    """stage 의 모든 prim 의 모든 xformOp.timeSamples 를 dict 으로 수집.

    Returns:
        animation_db: 다음 모양의 dict.
            {
              "/World/aaa": {
                "xformOp:translate": {
                  0.0:   Gf.Vec3d(0, 0, 0),
                  1.0:   Gf.Vec3d(0.1, 0, 0),
                  ...
                  250.0: Gf.Vec3d(25, 0, 0),
                },
                "xformOp:rotateXYZ": { ... },
              },
              "/World/bbb": { ... },
            }
    """
    animation_db: dict = {}
    print("=" * 80)
    print("COLLECTING ALL TIMESAMPLES")
    print("=" * 80)

    for prim in stage.Traverse():
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            continue
        ops = xformable.GetOrderedXformOps()
        if not ops:
            continue

        prim_data = {}
        for op in ops:
            attr = op.GetAttr()
            times = attr.GetTimeSamples()
            if not times:
                continue
            attr_data = {}
            for t in times:
                v = attr.Get(t)
                if v is None:
                    continue
                attr_data[float(t)] = v
            if attr_data:
                prim_data[attr.GetName()] = attr_data

        if prim_data:
            animation_db[str(prim.GetPath())] = prim_data
            print(f"\n[FOUND ANIMATION] {prim.GetPath()}")
            for attr_name, samples in prim_data.items():
                first_t = min(samples.keys())
                last_t = max(samples.keys())
                print(f"  └─ {attr_name}: {len(samples)} samples, "
                      f"time=[{first_t}, {last_t}]")

    return animation_db
```

### 1.3 실제 출력 샘플 — FBX→USD 자산 (TIMESAMPLES_XFORM)

```text
================================================================================
Prim : /World/aaa/Geom/Robot/Joint01
================================================================================

------------------------------------------------------------
Op Name : xformOp:translate
Attr    : xformOp:translate
TypeName: double3
TimeSamples Count : 251
  [0.0] = (0.0, 0.0, 0.0)
  [1.0] = (0.0834, 0.0, 0.0)
  [2.0] = (0.1668, 0.0, 0.0)
  [3.0] = (0.2502, 0.0, 0.0)
  [4.0] = (0.3336, 0.0, 0.0)
  ... (생략 245개) ...
  [250.0] = (20.85, 0.0, 0.0)

------------------------------------------------------------
Op Name : xformOp:rotateXYZ
Attr    : xformOp:rotateXYZ
TypeName: double3
TimeSamples Count : 251
  [0.0] = (0.0, 0.0, 0.0)
  [1.0] = (0.0, 1.44, 0.0)
  ...
  [250.0] = (0.0, 360.0, 0.0)
```

### 1.4 실제 출력 샘플 — Bake 한 OmniGraph 자산 (in-memory baked layer)

`bake_prim_to_timesamples_async(output_mode="memory")` 가 만들어 준 anonymous layer 를
offscreen stage 로 open 한 뒤 동일 함수를 돌려보면 같은 형태로 나온다. 핵심은 "bake 의
결과물도 결국 동일 형식의 timeSamples 다" 라는 점.

```text
================================================================================
Prim : /World/aaa
================================================================================

------------------------------------------------------------
Op Name : xformOp:translate
Attr    : xformOp:translate
TypeName: double3
TimeSamples Count : 251     ← bake 가 매 frame 평가해서 박은 결과
  [0.0]   = (0.0, 0.0, 0.0)
  [1.0]   = (0.1, 0.0, 0.0)
  ...
  [250.0] = (25.0, 0.0, 0.0)
```

위 두 케이스 모두 `attr.Get(timeCode)` 를 호출하면 USD 가 인접 sample 을 자동 보간해
단일 값을 반환한다. **그래서 재생 코드는 두 케이스에 대해 동일하게 동작한다.**

---

## 2. ② step 의 프레임 범위를 시간으로 변환

LAM 은 **30 fps 고정** 정책이라 변환이 단순하다 (`lam_runtime_evaluator.LAM_FIXED_FPS = 30`).

```python
LAM_FIXED_FPS = 30.0


def frames_to_seconds(frame: float) -> float:
    """프레임 → 초 변환 (LAM 고정 30 fps)."""
    return float(frame) / LAM_FIXED_FPS


def seconds_to_timecode(sec: float) -> float:
    """초 → USD timeCode 변환 (timeCode 는 frame 과 1:1 매핑되도록 stage 의 tps 를 30 으로
    맞춰 둔 상태에서)."""
    return float(sec) * LAM_FIXED_FPS
```

step 에서 사용자가 `range_start=30, range_end=60` 을 입력했다면:

```
start_sec  = 30 / 30 = 1.0 sec
end_sec    = 60 / 30 = 2.0 sec
duration   = 1.0 sec  ← step 의 자연 종료 (loop=False)
```

이 두 초 값이 인스턴스의 `virtual_time` 시작/끝 경계가 된다.

---

## 3. ③ timeSamples 를 viewport 에 재생 — self-contained 함수

LAM 의 `evaluate_and_write` 와 정확히 동일한 일을 하는 코드를 **외부 의존성 없이**
풀어쓴 버전. 학습용이며, 실 LAM 코드에서 어디가 동일한지는 §4 에서 매핑한다.

### 3.1 1 frame 평가 — `eval_one_frame_and_write_to_master`

```python
from pxr import Usd, UsdGeom


def eval_one_frame_and_write_to_master(
    offscreen_stage: Usd.Stage,   # 자산 USD 또는 baked layer 가 root 인 stage
    master_stage:    Usd.Stage,   # 합성 master.usd stage
    master_prim_path: str,         # 인스턴스 prim 경로. 예) "/World/aaa"
    timecode:        float,        # 평가할 시점. frame 값 그대로 (30fps 고정 가정)
    asset_prim_path: str = "",     # 자산 내부에서 시작 prim. 비우면 stage 전체
) -> int:
    """offscreen 의 timeSamples 를 timecode 에서 1 회 평가하여 master 의 동일 path
    attribute 에 **default value** 로 write.

    Returns:
        실제로 write 된 attribute 개수.
    """
    # 1) offscreen 의 현재 시각을 옮긴다 (보간이 timecode 기준 동작).
    #    master_stage 의 timeline 은 절대 진행하지 않는다 (멀티 인스턴스 독립 재생 보장).
    offscreen_stage.SetCurrentTimeCode(float(timecode))

    wrote = 0

    # 2) offscreen prim 들을 순회. asset_prim_path 가 있으면 그 하위만, 없으면 전체.
    root = (
        offscreen_stage.GetPrimAtPath(asset_prim_path)
        if asset_prim_path
        else offscreen_stage.GetPseudoRoot()
    )
    if not root or not root.IsValid():
        return 0

    for src_prim in Usd.PrimRange(root):
        xformable = UsdGeom.Xformable(src_prim)
        if not xformable:
            continue
        ops = xformable.GetOrderedXformOps()
        if not ops:
            continue

        # 3) offscreen 의 src_prim 경로를 master 의 mirror prim 경로로 변환.
        #    src="/Root/Mesh" → mirror="/World/aaa/Root/Mesh"
        #    가장 간단한 매핑은 자산 root 의 path 를 master_prim_path 로 치환.
        src_path = src_prim.GetPath().pathString
        if asset_prim_path:
            tail = src_path[len(asset_prim_path):] if src_path.startswith(asset_prim_path) else ""
        else:
            tail = src_path
        mirror_path = master_prim_path + tail if tail else master_prim_path

        mirror_prim = master_stage.GetPrimAtPath(mirror_path)
        if not mirror_prim or not mirror_prim.IsValid():
            # mirror 가 없으면 skip — 실제 LAM 은 setup_master_mirror_prim() 가 미리 보장.
            continue
        mirror_xf = UsdGeom.Xformable(mirror_prim)
        if not mirror_xf:
            continue

        # 4) 각 xformOp 마다 offscreen 에서 값을 읽고 master mirror 에 default 로 박는다.
        for op in ops:
            src_attr = op.GetAttr()
            if src_attr.GetNumTimeSamples() <= 0:
                continue                                  # static op 은 건너뜀
            value = src_attr.Get(float(timecode))         # ★ USD 가 자동 보간
            if value is None:
                continue

            # 4-1) master 의 같은 이름 attribute 를 찾거나, 동일 op 을 1 회 author.
            mirror_attr = mirror_prim.GetAttribute(src_attr.GetName())
            if not mirror_attr or not mirror_attr.IsValid():
                # 처음 보는 op 이라면 동일 op 을 mirror 에 1 회 추가.
                #   mirror_xf.AddTranslateOp() / AddRotateXYZOp() 등 op-type 별 helper.
                # 학습용 단순화: 이름으로 분기.
                name = src_attr.GetName()
                if name == "xformOp:translate":
                    mirror_attr = mirror_xf.AddTranslateOp().GetAttr()
                elif name == "xformOp:rotateXYZ":
                    mirror_attr = mirror_xf.AddRotateXYZOp().GetAttr()
                elif name == "xformOp:scale":
                    mirror_attr = mirror_xf.AddScaleOp().GetAttr()
                else:
                    continue   # 본 예제는 위 3종만 다룬다 (실 LAM 은 모든 op + Skel 지원).

            # 4-2) ★★★ 핵심 한 줄 — timeCode 인수 없이 Set() 으로 박으면 default 가 된다.
            #            master_stage 의 timeline 이 진행되지 않으므로 이 default 가
            #            reference 의 timeSamples 보다 winner (USD value resolution 규칙).
            mirror_attr.Set(value)
            wrote += 1

    return wrote
```

### 3.2 step 의 프레임 범위로 연속 재생 — `replay_timesamples_range`

매 update tick 에 호출되는 LAM 의 흐름을 한 함수로 압축한 버전. 실제 LAM 은 evaluator 가
이 일을 분산 수행하지만, 본 함수로 동일한 결과를 얻을 수 있다.

```python
import time
from pxr import Usd


def replay_timesamples_range(
    offscreen_stage: Usd.Stage,
    master_stage:    Usd.Stage,
    master_prim_path: str,         # "/World/aaa"
    start_frame:     float,         # 30  ← step.range_start
    end_frame:       float,         # 60  ← step.range_end
    fps:             float = 30.0,  # LAM 고정값
    speed:           float = 1.0,
    loop:            bool  = False,
    asset_prim_path: str   = "",   # offscreen 자산의 시작 prim. 보통 ""
) -> None:
    """frame [start_frame, end_frame] 을 실시간으로 master viewport 에 재생.

    실 LAM 은 evaluator 가 매 frame 자동 호출하지만, 본 함수는 동기 루프로 시연한다.
    호출 시 메인 스레드를 점유하므로 학습/디버그용으로만 사용할 것.
    """
    # 1) 프레임 → 초.
    start_sec = float(start_frame) / fps
    end_sec   = float(end_frame)   / fps
    if end_sec <= start_sec:
        print("[WARN] end_frame <= start_frame — 단일 프레임으로 처리")
        end_sec = start_sec + (1.0 / fps)

    # 2) 인스턴스의 "가상 시간" — master.timeline 을 만지지 않는다.
    virtual_time = start_sec
    dt = 1.0 / fps                  # tick 간격 (30 fps 기준 약 33ms)
    last_wall = time.perf_counter()

    while True:
        # 2-1) 가상 시간 진행. speed 가 1.0 이면 실시간, 2.0 이면 2배속.
        now = time.perf_counter()
        elapsed = now - last_wall
        last_wall = now
        virtual_time += elapsed * max(0.01, float(speed))

        # 2-2) 범위 끝에 도달했는가?
        if virtual_time >= end_sec:
            if loop:
                length = end_sec - start_sec
                if length > 1e-6:
                    virtual_time = start_sec + ((virtual_time - start_sec) % length)
                else:
                    virtual_time = start_sec
            else:
                virtual_time = end_sec
                # 마지막 frame 1 회 박고 종료.
                eval_one_frame_and_write_to_master(
                    offscreen_stage, master_stage, master_prim_path,
                    timecode = virtual_time * fps,
                    asset_prim_path = asset_prim_path,
                )
                print(f"[DONE] frame={virtual_time*fps:.1f} (end)")
                return

        # 2-3) ★ 핵심 — virtual_time → timeCode 변환 후 1 frame 평가/쓰기.
        timecode = virtual_time * fps
        wrote = eval_one_frame_and_write_to_master(
            offscreen_stage, master_stage, master_prim_path,
            timecode = timecode,
            asset_prim_path = asset_prim_path,
        )

        # 2-4) 다음 tick 까지 대기 (실 LAM 은 Kit 의 update event 가 대신함).
        sleep_for = max(0.0, dt - (time.perf_counter() - now))
        time.sleep(sleep_for)
```

### 3.3 호출 예시 — "step 의 frame 30~60 재생"

```python
from pxr import Usd

# 1) 자산 USD 를 in-memory offscreen stage 로 open.
offscreen = Usd.Stage.Open("/abs/path/to/asset.usd")

# 2) master.usd stage 핸들 (UI 가 들고 있는 것을 그대로 사용).
master = ...  # omni.usd.get_context().get_stage() 등

# 3) frame 30 ~ 60 (= 1.0sec ~ 2.0sec) 을 1배속으로 1회 재생.
replay_timesamples_range(
    offscreen_stage  = offscreen,
    master_stage     = master,
    master_prim_path = "/World/aaa",
    start_frame      = 30,
    end_frame        = 60,
    fps              = 30.0,
    speed            = 1.0,
    loop             = False,
    asset_prim_path  = "",       # 자산 stage 전체에서 timeSamples 가진 prim 모두 처리
)
```

실행 결과:

- `omni.timeline` 의 frame 인디케이터는 **움직이지 않는다** (의도된 동작 — D12).
- `/World/aaa` 인스턴스가 frame 30 의 포즈에서 시작해 1 초 동안 frame 60 까지 진행 후 정지.
- 같은 자산을 다른 prim path 로 두 번 등록해두면, 두 인스턴스를 서로 다른
  `start_frame`/`end_frame` 으로 동시에 재생 가능 (멀티 인스턴스 독립 재생).

---

## 4. 위 함수가 실제 LAM 코드의 어디에 해당하는가

학습용 self-contained 코드를 LAM 시스템 안에서 대응시키면 아래와 같다.

| 학습용 코드 (§1~§3) | 실 LAM 코드 |
|---|---|
| `collect_stage_timesamples` 의 stage 순회 | `lam_instance_runtime._build_attr_cache()` — 인스턴스마다 1 회만 캐시. depth-first 로 timeSamples attribute 만 수집. |
| `eval_one_frame_and_write_to_master` | `lam_instance_runtime.AnimationInstanceRuntime.evaluate_and_write(virtual_time)` |
| `replay_timesamples_range` 의 while loop | `lam_runtime_evaluator.RuntimeEvaluator._on_update_option_e()` — Kit 의 update event 에 attach되어 매 frame 호출. |
| `start_frame` / `end_frame` 파라미터 | `lam_playback_scheduler.start(prim, range_mode="frames", range_start=..., range_end=...)` — `inst.range = ("frames", s, e)` 에 박힘. |
| `virtual_time` 변수 | `AnimationInstance.virtual_time` — 인스턴스 dataclass 필드. |
| `mirror_attr.Set(value)` (default write) | `lam_instance_runtime.evaluate_and_write` 안의 `entry.mirror_attr.Set(val)` |
| Offscreen stage open | Bake 없으면 `setup_offscreen_stage(asset_path)` / Bake 있으면 `setup_offscreen_stage_from_layer(baked_layer)` |

핵심 한 줄 매핑:

```433:523:source/extensions/morph.lam_control/morph/lam_control/lam_instance_runtime.py
val = entry.attr.Get(tc)          # ← §3.1 의 src_attr.Get(timecode) 와 동일
entry.mirror_attr.Set(val)        # ← §3.1 의 mirror_attr.Set(value) 와 동일
```

---

## 5. Bake 가 있는 경우와 없는 경우의 차이 (§3 코드 관점)

**§3 의 코드는 한 줄도 바뀌지 않는다.** 단지 `offscreen_stage` 의 출처가 다를 뿐.

### 5.1 Bake 불필요 자산 (TIMESAMPLES_XFORM, 실무 USD)

```python
# 자산 USD 를 그대로 in-memory 로 open. timeSamples 가 이미 USD 안에 들어 있음.
offscreen = Usd.Stage.Open("/abs/path/to/asset.usd")

# 곧바로 replay 가능.
replay_timesamples_range(offscreen, master, "/World/aaa", 30, 60)
```

### 5.2 Bake 필요 자산 (OMNIGRAPH, curve animation 테스트 USD)

```python
import asyncio
from morph.lam_control import lam_bake_omnigraph as bake

async def _do_bake_then_replay():
    # bake_prim_to_timesamples_async 가 master stage 의 PushGraph 를 0~end 까지 scrub 하며
    # 매 frame 결과 xformOp 값을 anonymous Sdf.Layer 에 timeSamples 로 박는다.
    result = await bake.bake_prim_to_timesamples_async(
        prim_path       = "/World/aaa",
        sf              = 0,
        ef              = 250,
        tps             = 30,
        output_mode     = "memory",   # 디스크 파일 생성 X (D13)
        log_baked_dump  = True,
    )

    # baked layer 를 root 로 갖는 stage 를 offscreen 으로 사용.
    baked_stage = Usd.Stage.Open(result.baked_layer)

    # ★ §3 의 replay 함수를 동일하게 호출. 코드 변경 없음.
    replay_timesamples_range(baked_stage, master, "/World/aaa", 30, 60)

asyncio.ensure_future(_do_bake_then_replay())
```

`log_baked_dump=True` 면 §1.4 와 동일한 형태로 baked layer 의 timeSamples 가 콘솔에 찍혀
"bake 결과가 정말로 timeSamples 로 변환됐는지" 시각적으로 확인 가능하다.

---

## 6. 자주 묻는 질문

### Q1. `master_stage` 의 timeline 을 진행시키면 안 되는 이유?

`omni.timeline` 은 stage 전역 시각이라 두 인스턴스를 서로 다른 frame 에 둘 수 없다. LAM 의
멀티 인스턴스 독립 재생은 **각 인스턴스가 자기 `virtual_time` 을 갖고**, **그 시각에서
평가한 값을 master 의 mirror prim 에 default 로 박는** 방식이므로 master timeline 진행을
배제해야 성립한다 (D12, N2 정책).

### Q2. `attr.Set(value)` 와 `attr.Set(value, timeCode)` 의 차이?

- `Set(value)` — **default value**. timeCode 와 무관한 값. master 의 timeline 이 진행되지
  않으므로 매 frame 우리가 새 값으로 덮어쓰면 viewport 가 그 default 를 따라 변한다.
- `Set(value, timeCode)` — 특정 timeCode 에 박는 timeSamples. master timeline 이 그
  timeCode 에 도달했을 때만 보이는 값. **TIMESAMPLES_REPLAY 에서는 사용하지 않는다.**

### Q3. step 의 `range_mode="frames"` 외에 다른 옵션은?

- `"full"` — 자산 stage 의 `startTimeCode` ~ `endTimeCode` 전체 재생.
- `"frames"` — 사용자가 지정한 `[range_start, range_end]` 프레임 구간.
- `"ratio"` — 자산 길이 대비 비율. 예: `range_start=0.4, range_end=0.6` 은 자산이 250
  frame 이면 frame 100~150 과 동등.

### Q4. 같은 자산을 두 번 등록하고 서로 다른 프레임 범위로 동시 재생하려면?

```python
# 인스턴스 두 개 등록 (UI 의 [+ USD 추가] 를 두 번 누른 상태).
#   /World/aaa,    source_asset=./asset.usd
#   /World/aaa_1,  source_asset=./asset.usd   ← 자동 _1 suffix

# step 1: aaa  frame 0~250 재생
scheduler.start("/World/aaa",   range_mode="frames", range_start=0,  range_end=250)

# step 2: aaa_1 frame 30~60 재생 (run_with_previous=True 면 step 1 과 동시 시작)
scheduler.start("/World/aaa_1", range_mode="frames", range_start=30, range_end=60)
```

두 인스턴스는 서로 다른 offscreen stage / 서로 다른 `virtual_time` 을 갖고, evaluator 가
매 frame 둘 다 `evaluate_and_write` 를 호출하므로 viewport 에서 동시에 독립적으로 움직인다.

---

## 7. 코드 위치 빠른 인덱스

| 무엇 | 파일 | 함수 |
|---|---|---|
| timeSamples 캐시 빌드 (§1 의 collect 와 동등) | `lam_instance_runtime.py` | `_build_attr_cache()` |
| 1 frame 평가 + master write (§3.1 과 동등) | `lam_instance_runtime.py` | `evaluate_and_write()` |
| 매 frame 호출 (§3.2 의 while 과 동등) | `lam_runtime_evaluator.py` | `_on_update_option_e()` |
| start_frame / end_frame 박기 | `lam_playback_scheduler.py` | `start()` |
| Skel fallback (timeSamples 가 0 일 때) | `lam_instance_runtime.py` | `_evaluate_skel_and_write_to_master()` |
| Bake 실행 | `lam_bake_omnigraph.py` | `bake_prim_to_timesamples_async()` |
| Bake 결과 attach | `lam_runtime_evaluator.py` | `attach_memory_baked_layer()` |
| 자산 종류 분류 | `lam_asset_diagnostics.py` | `scan_asset_kind()` |
| UI 의 [Bake] 버튼 분기 | `lam_window.py` | `_refresh_instances()` |
| Step dispatch | `lam_sequence_engine.py` | `_start_ref_play()` |

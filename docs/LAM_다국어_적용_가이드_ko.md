# LAM 다국어 적용 가이드 (kr / en / ch)

`lam_control_1` Viewport에 보이는 **고정 문구 4개**를 한국어·영어·중국어로 바꾸고, 웹이 `kr` / `en` / `ch`를 보내면 **바로 UI에 반영**하는 방법이다.

이 문서는 구현 전에 합의한 내용을 정리한 설계 가이드다. 코드는 아직 넣지 않는다.

화면1·화면2는 **같은 언어**를 쓴다. 화면별로 언어를 나누지 않는다.

---

## 1. 이번에 바꿀 문구 (4개)

| 위치 | 종류 | 지금 화면에 나오는 글 | 예정 key |
|---|---|---|---|
| 좌상단 로딩 HUD | 2D (`omni.ui`) | `Loading data...` | `loading_data` |
| FOUP 상태보기 | 3D (`sc.Label`) | `반출` | `foup_unload` |
| FOUP 상태보기 | 3D (`sc.Label`) | `공정진행` | `foup_in_process` |
| FOUP 상태보기 | 3D (`sc.Label`) | `공정완료` | `foup_done` |

숫자는 번역하지 않는다.

- 로딩: `데이터 로딩중... 37%` 처럼 **앞 문구만** 바꾸고 `%` 숫자는 그대로
- FOUP: `반출` / `11/25`, `공정진행` / `21`, `공정완료` / `0` 에서 **왼쪽 이름만** 바꾸고 오른쪽 숫자는 그대로

lot id(`FOUP1` 등)는 장비 식별자라 번역하지 않는다.

나중에 재생 버튼에 `재생`, 실패 시 `실패`가 붙으면 같은 파일에 key만 추가하면 된다. 이번 범위에는 넣지 않는다.

---

## 2. 목표 동작

1. 문구는 UI 코드에 직접 쓰지 않고, **별도 언어 파일**의 key로 가져온다.
2. 웹이 언어 코드를 보내면 Kit이 현재 언어를 바꾸고, **이미 떠 있는 2D·3D 라벨을 즉시 다시 그린다.** Kit 재시작은 필요 없다.
3. 웹이 언어를 안 보내면 Kit 기본 언어로 동작한다. (기본값 제안: `en` — 지금 로딩 HUD가 영어. 운영이 한국어 우선이면 `kr`로 바꿔도 된다.)
4. 중국어가 `???`로 나오는 문제는 번역 파일이 아니라 **폰트** 문제다. 한·중 글자가 있는 폰트를 2D·3D 라벨에 같이 넣는다.

---

## 3. 언어 코드

웹 → Kit 값은 아래 세 가지만 받는다.

| 웹이 보내는 값 | 언어 |
|---|---|
| `kr` | 한국어 |
| `en` | 영어 |
| `ch` | 중국어 (간체) |

대소문자는 소문자로 맞춘다. `KR` → `kr`.  
없는 값이면 바꾸지 않고 지금 언어를 유지한다. (또는 실패 응답 `code: 1`)

---

## 4. 언어 파일 (key → 번역)

문구와 폰트는 **다른 파일**이다.

- 문구: JSON (UTF-8)
- 폰트: `.otf` / `.ttf` (한·중·영 글리프)

### 4.1 위치 (제안)

```
source/extensions/morph.lam_control_1/
  data/
    i18n/
      lam_ui_strings.json
    fonts/
      NotoSansCJKsc-Regular.otf   # 이름은 실제 넣는 파일에 맞출 것
  morph/lam_control_1/
    lam_i18n.py                   # 로드·현재 언어·t(key) · UI 갱신 알림
```

`lam_ui_strings.json`만 고치면 번역을 바꿀 수 있게 한다. 코드에 한·영·중 문장을 흩어 두지 않는다.

### 4.2 파일 형식

```json
{
  "loading_data": {
    "kr": "데이터 로딩중...",
    "en": "Loading data...",
    "ch": "数据加载中..."
  },
  "foup_unload": {
    "kr": "반출",
    "en": "Unload",
    "ch": "搬出"
  },
  "foup_in_process": {
    "kr": "공정진행",
    "en": "Processing",
    "ch": "工艺进行"
  },
  "foup_done": {
    "kr": "공정완료",
    "en": "Complete",
    "ch": "工艺完成"
  }
}
```

위 영·중 문장은 **임시 초안**이다. 현장 용어가 다르면 JSON만 고친다.

### 4.3 Kit에서 읽는 방법

`lam_i18n.py`가 할 일:

- 기동 시 JSON을 한 번 로드한다.
- `current_lang` (`kr`/`en`/`ch`)을 기억한다.
- `t("foup_unload")` → 지금 언어의 문자열을 돌려준다. key가 없으면 영어, 그것도 없으면 key 이름.
- `set_lang("ch")` 하면 `current_lang`을 바꾸고, **구독자에게 “언어 바뀜”을 알려** 2D·3D가 라벨을 다시 쓴다.

UI 쪽 예:

```python
# 로딩 HUD (2D)
self._label.text = f"{t('loading_data')} {pct}%"

# FOUP 패널 (3D) 왼쪽 이름
left.text = t("foup_unload")      # 1행
left.text = t("foup_in_process")  # 2행
left.text = t("foup_done")        # 3행
```

지금 하드코딩된 위치:

- 2D: `lam_federation_load_hud.py` — `"Loading data... 0%"`, `f"Loading data... {pct}%"`
- 3D: `lam_viewport_foup_status_3d.py` — `_BODY_ROW_LABELS = ("반출", "공정진행", "공정완료")`

이 네 곳을 `t(key)`로 바꾼다. 로그 문자열(`[LAM/federation] HUD Loading data... unchanged` 등)은 운영자용이므로 이번 범위에서 번역하지 않아도 된다.

---

## 5. 웹 API (실시간 언어 변경)

기존 HyView와 같은 봉투를 쓴다. `{code, message, data}`  
Kit 시뮬·로딩·숨김은 언어 요청과 무관하다. 문구만 바꾼다.

### 5.1 요청 — `T2V_set_language` (가칭)

```json
{ "lang": "ch" }
```

| 키 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `lang` | string | 예 | `kr` / `en` / `ch` |

`case`는 없다. 화면1·2 모두 같은 언어.

기존 `T2V_control_simulation`에 `lang`을 섞지 않는 것을 권한다. 제어(prim 숨김·배속)와 언어는 별개 이벤트다.

### 5.2 응답 — `V2T_response_set_language`

```json
{ "code": 0, "message": "success", "data": { "lang": "ch" } }
```

실패 예: `lang`이 `kr`/`en`/`ch`가 아님.

```json
{ "code": 1, "message": "invalid lang: ja", "data": { "lang": "ja" } }
```

### 5.3 Kit가 받은 뒤

1. `lam_i18n.set_lang("ch")`
2. 로딩 HUD가 떠 있으면 `Loading data...` → `数据加载中...` (숫자는 유지)
3. FOUP 패널이 떠 있으면 `반출` → `搬出` 등 세 줄 즉시 갱신
4. 재생 중이어도 다음 틱을 기다리지 않고 **바로** `.text`를 바꾼다
5. V2T 성공 응답

웹이 이 이벤트를 안 보내면 지금처럼 기본 언어로만 보인다.

---

## 6. 2D와 3D가 다른 이유

같은 JSON·같은 `t(key)`를 쓰되, **그리는 API가 다르다.**

| | 2D 로딩 HUD | 3D FOUP 패널 |
|---|---|---|
| 위젯 | `omni.ui.Label` | `omni.ui.scene.Label` (`sc.Label`) |
| 파일 | `lam_federation_load_hud.py` | `lam_viewport_foup_status_3d.py` |
| 폰트 넣는 곳 | `style={"font": 경로, "font_size": …}` | `sc.Label(..., font=경로)` |
| 갱신 | `_label.text = t("loading_data") + …` | 각 행 `left.text = t(...)` |

언어가 바뀌면:

- 2D: 로딩 패널이 있으면 라벨만 다시 쓴다. 위젯을 새로 만들지 않아도 된다.
- 3D: 이미 있는 `sc.Label`의 `.text`만 바꾼다. 패널 레이아웃(104가 아닌 현재 220×178, 색 띠, 좌우 정렬)은 그대로 둔다.

---

## 7. 중국어가 `???`로 나오는 문제

번역 JSON에 한자를 넣어도, 라벨 폰트에 그 글리프가 없으면 `???` 또는 빈 네모로 나온다.  
지금 FOUP 3D는 기본 씬 폰트라 한글·한자가 깨질 수 있다. 2D `omni.ui`도 OS/Kit 기본 폰트에 따라 깨질 수 있다.

### 7.1 해야 할 일

1. 한·영·중(간체)이 들어 있는 폰트 파일을 확장 `data/fonts/`에 넣는다.  
   예: Google Noto Sans CJK (SIL OFL). **한 파일로 kr/en/ch를 모두 커버**하면 언어가 바뀌어도 폰트 경로는 그대로다.
2. 기동 시 그 파일의 **절대 경로**를 잡아 2D 스타일·3D `font=`에 넘긴다.
3. 로딩 HUD와 FOUP 패널 **둘 다** 같은 폰트를 쓴다. 한쪽만 넣으면 다른 쪽에서 또 `???`가 난다.
4. JSON은 **UTF-8**로 저장한다. Windows에서 시스템 기본 코드페이지로 저장하면 한자가 깨진 채로 로드될 수 있다.

### 7.2 확인

- `ch` 요청 후 FOUP `搬出` / `工艺进行` / `工艺完成`이 한자로 보이는지
- 로딩 `数据加载中... 12%`가 한자로 보이는지
- `kr`로 돌렸을 때 `반출` 등 한글이 보이는지
- 폰트 파일이 없으면 영어만 보이고, Kit는 죽지 않게 한다 (경로 실패 시 로그 + 기본 폰트)

라이선스: Noto를 넣으면 OFL 고지를 확장 문서나 `data/fonts/README`에 남긴다.

---

## 8. 런타임 흐름

```
웹  T2V_set_language { lang: "ch" }
        │
        ▼
LamHandler → lam_i18n.set_lang("ch")
        │
        ├─ lam_federation_load_hud  : 2D 라벨 text + font
        └─ lam_viewport_foup_status_3d : 3D 세 줄 left.text + font
        │
        ▼
V2T_response_set_language { data: { lang: "ch" } }
```

언어 변경은 fetch·재생을 다시 하지 않는다. 떠 있는 글자만 바꾼다.

FOUP 숫자는 기존처럼 0.2초 주기로 집계를 갱신해도 된다. 그때도 `t(key)`로 이름을 붙이면 언어가 유지된다.

---

## 9. 구현 순서 (코드 넣을 때)

1. `data/i18n/lam_ui_strings.json` 생성 (위 초안)
2. CJK 폰트를 `data/fonts/`에 넣고 경로 헬퍼
3. `lam_i18n.py`: 로드, `t()`, `set_lang()`, 구독
4. 로딩 HUD 4곳의 `Loading data`를 `t("loading_data")`로 교체 + 폰트
5. FOUP `_BODY_ROW_LABELS`를 key 목록으로 바꾸고 `t()` + 폰트
6. `T2V_set_language` / `V2T_response_set_language`를 `hyview_event_contract`·`LamHandler`에 등록
7. `ch`로 한자가 보이는지, `kr`/`en` 전환이 재생 중에도 되는지 확인

---

## 10. 웹에서 할 일

- 사용자 언어가 바뀌면 `T2V_set_language`에 `lang: "kr"|"en"|"ch"`만 보낸다.
- 성공 응답의 `data.lang`으로 UI 언어를 맞출 수 있다.
- 보내지 않으면 Kit 기본 언어가 유지된다.
- 이 요청으로 시뮬을 시작하거나 멈추지 않는다.

---

## 11. 이번에 하지 않는 것

- 화면1·화면2 서로 다른 언어
- STATUS 패널, 기기정보, 웨이퍼 번호, Kit 설정 창, 콘솔 로그 번역
- 재생 / 실패 버튼 문구 (생기면 같은 JSON에 key 추가)
- 웹 페이지 자체의 i18n (Kit Viewport 문구만)

---

## 12. 요약

| 항목 | 내용 |
|---|---|
| 대상 | `loading_data`, `foup_unload`, `foup_in_process`, `foup_done` |
| 저장 | `lam_ui_strings.json` (key × kr/en/ch) |
| 적용 | `t(key)` → 2D `omni.ui.Label` / 3D `sc.Label` |
| 웹 | `T2V_set_language` `{ "lang": "kr"\|"en"\|"ch" }` |
| 화면 | 1·2 공통 |
| `???` | 문구 파일이 아니라 CJK 폰트를 2D·3D에 연결 |
| 숫자 | `%`, `11/25`, `21`, `0` 은 번역하지 않음 |

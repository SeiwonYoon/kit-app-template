# HyView 로컬 스트리밍 클라이언트 (방법 A)

실무 HyView 와 **동일한 경로**로 Kit `ebs_handler` 를 검증하는 브라우저 클라이언트입니다.

```
브라우저 (본 앱)
  ├─ WebRTC 영상  ← omni.kit.livestream.webrtc
  └─ T2V / V2T    ← omni.kit.livestream.messaging → morph.hyview_messaging / EBSHandler
```

HTTP 브리지(8720) / `tbs_kit_remote` 는 **제거**되었습니다.

## 사전 조건

- Node.js 18+
- Chromium 계열 브라우저
- Kit: **`morph.editor_streaming.kit`** 실행 (스트리밍 + messaging 확장 로드)

확장 로드 확인 (Kit 콘솔):

- `[morph.hyview_messaging] started`
- `EBSHandler registered ... incoming events`

## 설치 · 실행

```bash
cd source/extensions/morph.hyview_messaging/web/hyview_client
npm install   # NVIDIA .npmrc 필요 — README 참고
npm run dev
```

브라우저: http://localhost:5173

1. **스트림 연결** — Kit livestream (기본 `127.0.0.1:49100`) 에 접속
2. EBS 패널에서 EP / EBS / 시뮬 시작 / Play·Pause — `T2V_*` 전송
3. **마지막 V2T** / 로그에서 `V2T_*` 응답 확인

## stream.config.json

로컬 Kit 주소·시그널링 포트. Kit livestream 설정과 맞출 것.

```json
{
  "source": "local",
  "local": {
    "server": "127.0.0.1",
    "signalingPort": 49100
  }
}
```

## 실무 웹과의 관계

| | 실무 HyView | 본 로컬 클라이언트 |
|--|-------------|-------------------|
| 영상 | livestream | livestream (동일) |
| API | `T2V_*` / `V2T_*` | **동일** (`src/hyviewMessaging.ts`) |
| Kit | `ebs_handler` | **동일** |

실무 웹은 `hyviewMessaging.ts` 와 같은 envelope 형식을 사용하면 됩니다:

```json
{ "event_type": "T2V_request_eqp_change", "payload": { "case": 0, "ep_count": 2 } }
```

## 트러블슈팅

| 증상 | 확인 |
|------|------|
| 스트림 연결 실패 | `morph.editor_streaming` 으로 Kit 실행 여부, signaling 포트 |
| T2V 보냈는데 Kit 무반응 | `morph.hyview_messaging` 로드, `morph.tbs_control_2` 로드 |
| V2T 안 옴 | Kit 콘솔 `[EBSHandler]` 로그, 이벤트 이름 오타 |

## 참고

- NVIDIA [Application Streaming](https://docs.omniverse.nvidia.com/kit/docs/kit-app-template/latest/docs/streaming.html)
- OV Web SDK Create sample (web-viewer-sample 후속)

import { useCallback, useEffect, useRef, useState } from "react";
import AppStream from "./AppStream";
import EbsSimPanel from "./EbsSimPanel";
import {
  type HyViewEnvelope,
  type HyViewTransportMode,
  type V2TWrapper,
  checkHyViewHttpHealth,
  fetchHyViewV2TEvents,
  getHyViewTransportMode,
  setHyViewTransportMode,
} from "./hyviewMessaging";

const HTTP_POLL_MS = 400;

export default function App() {
  const [transportMode, setTransportMode] = useState<HyViewTransportMode>(() => getHyViewTransportMode());
  const [streamConnected, setStreamConnected] = useState(false);
  const [httpConnected, setHttpConnected] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [lastV2T, setLastV2T] = useState("");
  const v2tSinceRef = useRef(0);

  const onLog = useCallback((line: string) => {
    const stamp = new Date().toLocaleTimeString();
    setLogLines((prev) => [`[${stamp}] ${line}`, ...prev].slice(0, 120));
  }, []);

  const applyV2T = useCallback(
    (eventType: string, body: V2TWrapper) => {
      onLog(`${eventType} code=${body.code} msg=${body.message ?? ""}`);
      setLastV2T(JSON.stringify({ event_type: eventType, ...body }, null, 2));
    },
    [onLog],
  );

  const onKitMessage = useCallback(
    (msg: HyViewEnvelope) => {
      if (!msg.event_type.startsWith("V2T_")) {
        return;
      }
      applyV2T(msg.event_type, msg.payload as V2TWrapper);
    },
    [applyV2T],
  );

  const switchMode = (mode: HyViewTransportMode) => {
    setHyViewTransportMode(mode);
    setTransportMode(mode);
    v2tSinceRef.current = 0;
    setLastV2T("");
    onLog(`transport → ${mode}`);
  };

  useEffect(() => {
    if (transportMode !== "http") {
      setHttpConnected(false);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      const ok = await checkHyViewHttpHealth();
      if (cancelled) {
        return;
      }
      setHttpConnected(ok);
      if (!ok) {
        return;
      }
      try {
        const since = v2tSinceRef.current;
        const { events, latest_seq } = await fetchHyViewV2TEvents(since);
        if (cancelled || events.length === 0) {
          return;
        }
        for (const ev of events) {
          applyV2T(ev.event_type, ev.payload);
        }
        v2tSinceRef.current = latest_seq;
      } catch (err) {
        onLog(`[WARN] V2T poll failed: ${String(err)}`);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), HTTP_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [transportMode, applyV2T, onLog]);

  const messagingReady = transportMode === "http" ? httpConnected : streamConnected;

  return (
    <div className="app-layout">
      <header>
        <h1>Morph HyView — EBS API 테스트</h1>
        <p>
          <strong>HTTP 디버그</strong> (기본): <code>morph.editor.kit</code> + 포트 8721 — 스트리밍 불필요
          {" · "}
          <strong>스트리밍</strong>: <code>morph.editor_streaming.kit</code> + WebRTC
        </p>
        <div className="row mode-switch">
          <label>
            <input
              type="radio"
              name="transport"
              checked={transportMode === "http"}
              onChange={() => switchMode("http")}
            />
            HTTP 디버그 (8721)
          </label>
          <label>
            <input
              type="radio"
              name="transport"
              checked={transportMode === "stream"}
              onChange={() => switchMode("stream")}
            />
            Livestream
          </label>
          <span className={`status-pill ${messagingReady ? "ok" : "warn"}`}>
            {transportMode === "http"
              ? httpConnected
                ? "HTTP 연결됨"
                : "HTTP 대기 (Kit 실행·TBS_HYVIEW_DEBUG_HTTP=1)"
              : streamConnected
                ? "스트림 연결됨"
                : "스트림 대기"}
          </span>
        </div>
      </header>
      <div className={`main-grid ${transportMode === "http" ? "http-only" : ""}`}>
        {transportMode === "stream" ? (
          <AppStream onConnected={setStreamConnected} onKitMessage={onKitMessage} />
        ) : (
          <section className="http-info-panel">
            <h2>HTTP 디버그 모드</h2>
            <p>Kit 콘솔에 아래 로그가 보이면 준비 완료:</p>
            <pre>{`[HyViewDebugHttp] listening http://127.0.0.1:8721`}</pre>
            <p>
              T2V: <code>POST /hyview/t2v</code> · V2T: <code>GET /hyview/v2t?since=N</code> (자동 폴링{" "}
              {HTTP_POLL_MS}ms)
            </p>
          </section>
        )}
        <EbsSimPanel messagingReady={messagingReady} transportMode={transportMode} onLog={onLog} lastV2T={lastV2T} />
      </div>
      <section className="log-panel">
        <h3>이벤트 로그</h3>
        <pre>{logLines.join("\n") || "(없음)"}</pre>
      </section>
    </div>
  );
}

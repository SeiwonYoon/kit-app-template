import { useCallback, useState } from "react";
import AppStream from "./AppStream";
import EbsSimPanel from "./EbsSimPanel";
import { type HyViewEnvelope, type V2TWrapper } from "./hyviewMessaging";

export default function App() {
  const [streamConnected, setStreamConnected] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [lastV2T, setLastV2T] = useState("");

  const onLog = useCallback((line: string) => {
    const stamp = new Date().toLocaleTimeString();
    setLogLines((prev) => [`[${stamp}] ${line}`, ...prev].slice(0, 80));
  }, []);

  const onKitMessage = useCallback(
    (msg: HyViewEnvelope) => {
      if (!msg.event_type.startsWith("V2T_")) {
        return;
      }
      const body = msg.payload as V2TWrapper;
      onLog(`${msg.event_type} code=${body.code} msg=${body.message}`);
      setLastV2T(JSON.stringify(body, null, 2));
    },
    [onLog],
  );

  return (
    <div className="app-layout">
      <header>
        <h1>Morph HyView — 로컬 스트리밍 + EBS API 테스트</h1>
        <p>
          방법 A: <code>morph.editor_streaming</code> Kit + livestream messaging +{" "}
          <code>ebs_handler</code> (실무와 동일 경로)
        </p>
      </header>
      <div className="main-grid">
        <AppStream onConnected={setStreamConnected} onKitMessage={onKitMessage} />
        <EbsSimPanel
          streamConnected={streamConnected}
          onLog={onLog}
          lastV2T={lastV2T}
        />
      </div>
      <section className="log-panel">
        <h3>이벤트 로그</h3>
        <pre>{logLines.join("\n") || "(없음)"}</pre>
      </section>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AppStreamer,
  StreamType,
  type DirectConfig,
  type StreamEvent,
  type StreamProps,
} from "@nvidia/omniverse-webrtc-streaming-library";
import streamConfig from "../stream.config.json";
import { parseKitMessage, type HyViewEnvelope } from "./hyviewMessaging";

type Props = {
  onConnected: (connected: boolean) => void;
  onKitMessage: (msg: HyViewEnvelope) => void;
};

const VIDEO_ID = "hyview-stream-video";

export default function AppStream({ onConnected, onKitMessage }: Props) {
  const connectingRef = useRef(false);
  const [status, setStatus] = useState("disconnected");
  const [error, setError] = useState<string | null>(null);

  const handleCustomEvent = useCallback(
    (event: { event_type?: string; payload?: unknown }) => {
      const raw =
        typeof event === "string"
          ? event
          : JSON.stringify({
              event_type: event.event_type,
              payload: event.payload ?? {},
            });
      const parsed = parseKitMessage(raw);
      if (parsed) {
        onKitMessage(parsed);
      }
    },
    [onKitMessage],
  );

  const connect = useCallback(async () => {
    if (connectingRef.current || status === "connected") {
      return;
    }
    setError(null);
    setStatus("connecting");
    connectingRef.current = true;

    const local = streamConfig.local;
    const directConfig: DirectConfig = {
      videoElementId: VIDEO_ID,
      authenticate: true,
      maxReconnects: 20,
      signalingServer: local.server,
      signalingPort: local.signalingPort,
      mediaServer: local.server,
      ...(local.mediaPort != null ? { mediaPort: local.mediaPort } : {}),
      nativeTouchEvents: true,
      width: 1920,
      height: 1080,
      fps: 60,
      onStart: (message: StreamEvent) => {
        if (message.action === "start" && message.status === "success") {
          setStatus("connected");
          onConnected(true);
        }
        if (message.status === "error") {
          setError(String(message.info ?? "stream start failed"));
          setStatus("error");
          onConnected(false);
        }
      },
      onStop: () => {
        setStatus("disconnected");
        onConnected(false);
      },
      onCustomEvent: handleCustomEvent,
    };

    const streamProps: StreamProps = {
      streamSource: StreamType.DIRECT,
      streamConfig: directConfig,
    };

    try {
      await AppStreamer.connect(streamProps);
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : String(exc);
      setError(msg);
      setStatus("error");
      onConnected(false);
    } finally {
      connectingRef.current = false;
    }
  }, [handleCustomEvent, onConnected, status]);

  const disconnect = useCallback(() => {
    try {
      AppStreamer.stop();
    } catch {
      /* ignore */
    }
    setStatus("disconnected");
    onConnected(false);
  }, [onConnected]);

  useEffect(() => {
    return () => {
      try {
        AppStreamer.stop();
      } catch {
        /* ignore */
      }
    };
  }, []);

  return (
    <div className="stream-panel">
      <div className="stream-toolbar">
        <span className={`pill pill-${status}`}>{status}</span>
        <button type="button" onClick={connect} disabled={status === "connected" || status === "connecting"}>
          스트림 연결
        </button>
        <button type="button" onClick={disconnect} disabled={status !== "connected"}>
          연결 해제
        </button>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <video
        id={VIDEO_ID}
        className="stream-video"
        autoPlay
        playsInline
        muted
        tabIndex={-1}
      />
    </div>
  );
}

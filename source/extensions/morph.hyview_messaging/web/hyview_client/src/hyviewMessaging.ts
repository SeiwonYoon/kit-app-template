/**
 * HyView T2V / V2T — 실무·로컬 공용 메시징 계약.
 *
 * Kit 쪽 ebs_handler.py 와 동일한 event_type / payload 를 사용한다.
 *
 * 전송 모드:
 * - stream: omni.kit.livestream.messaging (AppStreamer.sendMessage) — morph.editor_streaming.kit
 * - http:   HyView Debug HTTP (127.0.0.1:8721) — morph.editor.kit, 스트리밍 불필요
 */

import { AppStreamer } from "@nvidia/omniverse-webrtc-streaming-library";

export type HyViewEnvelope = {
  event_type: string;
  payload: Record<string, unknown>;
};

export type V2TWrapper = {
  code?: number;
  message?: string;
  data?: Record<string, unknown>;
};

export type HyViewTransportMode = "stream" | "http";

export type HyViewV2TRecord = {
  seq: number;
  ts: number;
  event_type: string;
  payload: V2TWrapper;
};

const DEFAULT_HTTP_BASE = "http://127.0.0.1:8721";

let transportMode: HyViewTransportMode =
  typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mode") === "stream"
    ? "stream"
    : "http";

let httpBaseUrl = DEFAULT_HTTP_BASE;

export function getHyViewTransportMode(): HyViewTransportMode {
  return transportMode;
}

export function setHyViewTransportMode(mode: HyViewTransportMode, baseUrl?: string): void {
  transportMode = mode;
  if (baseUrl) {
    httpBaseUrl = baseUrl.replace(/\/$/, "");
  }
}

export function getHyViewHttpBaseUrl(): string {
  return httpBaseUrl;
}

export async function checkHyViewHttpHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${httpBaseUrl}/hyview/health`, { method: "GET" });
    if (!res.ok) {
      return false;
    }
    const body = (await res.json()) as { ok?: boolean };
    return body.ok === true;
  } catch {
    return false;
  }
}

export async function clearHyViewV2TBuffer(): Promise<void> {
  await fetch(`${httpBaseUrl}/hyview/v2t`, { method: "DELETE" });
}

export async function fetchHyViewV2TEvents(since: number): Promise<{
  events: HyViewV2TRecord[];
  latest_seq: number;
}> {
  const res = await fetch(`${httpBaseUrl}/hyview/v2t?since=${encodeURIComponent(String(since))}`);
  if (!res.ok) {
    throw new Error(`GET /hyview/v2t failed: ${res.status}`);
  }
  const body = (await res.json()) as {
    events?: HyViewV2TRecord[];
    latest_seq?: number;
  };
  return {
    events: Array.isArray(body.events) ? body.events : [],
    latest_seq: typeof body.latest_seq === "number" ? body.latest_seq : since,
  };
}

export async function sendT2V(eventType: string, payload: Record<string, unknown>): Promise<void> {
  if (transportMode === "http") {
    const res = await fetch(`${httpBaseUrl}/hyview/t2v`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: eventType, payload }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`POST /hyview/t2v failed: ${res.status} ${text}`);
    }
    return;
  }
  const message: HyViewEnvelope = { event_type: eventType, payload };
  AppStreamer.sendMessage(JSON.stringify(message));
}

export function parseKitMessage(raw: string): HyViewEnvelope | null {
  try {
    const obj = JSON.parse(raw) as HyViewEnvelope;
    if (!obj || typeof obj.event_type !== "string") {
      return null;
    }
    return {
      event_type: obj.event_type,
      payload: (obj.payload as Record<string, unknown>) ?? {},
    };
  } catch {
    return null;
  }
}

/** T2V — EP 개수 변경 */
export async function requestEqpChange(caseIndex: number, epCount: number, eqpId = "LOCAL_TEST"): Promise<void> {
  await sendT2V("T2V_request_eqp_change", { case: caseIndex, eqp_id: eqpId, ep_count: epCount });
}

/** T2V — EBS on/off */
export async function requestEbsEnable(caseIndex: number, ebsEnable: boolean): Promise<void> {
  await sendT2V("T2V_request_ebs_enable", { case: caseIndex, ebs_enable: ebsEnable });
}

/** T2V — 시뮬 시작 (configs[0]=case0, configs[1]=case1 settings_snapshot) */
export async function requestStartSimulation(
  configs: [Record<string, unknown>, Record<string, unknown>],
): Promise<void> {
  await sendT2V("T2V_request_start_simulation", { configs });
}

/** T2V — play / pause / speed */
export async function requestControlSimulation(
  action: "play" | "pause",
  speed?: number,
): Promise<void> {
  const payload: Record<string, unknown> = { action };
  if (speed !== undefined) {
    payload.speed = speed;
  }
  await sendT2V("T2V_request_control_simulation", payload);
}

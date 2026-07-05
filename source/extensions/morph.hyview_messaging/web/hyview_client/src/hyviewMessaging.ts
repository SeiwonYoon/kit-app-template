/**
 * HyView T2V / V2T — 실무·로컬 공용 메시징 계약.
 *
 * Kit 쪽 ebs_handler.py 와 동일한 event_type / payload 를 사용한다.
 * 전송: omni.kit.livestream.messaging (AppStreamer.sendMessage).
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

export function sendT2V(eventType: string, payload: Record<string, unknown>): void {
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
export function requestEqpChange(caseIndex: number, epCount: number, eqpId = "LOCAL_TEST"): void {
  sendT2V("T2V_request_eqp_change", { case: caseIndex, eqp_id: eqpId, ep_count: epCount });
}

/** T2V — EBS on/off */
export function requestEbsEnable(caseIndex: number, ebsEnable: boolean): void {
  sendT2V("T2V_request_ebs_enable", { case: caseIndex, ebs_enable: ebsEnable });
}

/** T2V — 시뮬 시작 (config[0]=case0, config[1]=case1 settings_snapshot) */
export function requestStartSimulation(
  config: [Record<string, unknown>, Record<string, unknown>],
): void {
  sendT2V("T2V_request_start_simulation", { config });
}

/** T2V — play / pause / speed */
export function requestControlSimulation(
  action: "play" | "pause",
  speed?: number,
): void {
  const payload: Record<string, unknown> = { action };
  if (speed !== undefined) {
    payload.speed = speed;
  }
  sendT2V("T2V_request_control_simulation", payload);
}

/**

 * =============================================================================

 * TbsControlTab.tsx — TBS Kit 원격 제어 패널 (단일 React 파일)

 * =============================================================================

 *

 * [이 파일이 하는 일]

 *   회사 웹 페이지의 `<div class="control-tab">` 안에 넣어, Kit(TBS 확장)을

 *   HTTP로 원격 조작하는 UI 전체를 제공합니다.

 *   Viewport 스트리밍(WebRTC)은 이 파일과 무관 — 오른쪽 kit-app-streaming-area 담당.

 *

 * [데이터 흐름]

 *

 *   브라우저(control-tab)                    Kit 프로세스

 *   ─────────────────────                    ────────────

 *   입력 폼 (form state)  ──POST /api/command──► kit_remote_http_bridge.py

 *                                                    └─► control_window.py 등

 *   포트/로그 표시        ◄──GET  /api/state──────  (Kit UI 라벨 텍스트 스냅샷)

 *   USD 콤보 목록         ◄──GET  /api/resources──  (샘플 USD 목록)

 *

 * [파일 내부 구조]

 *   1) CONTROL_TAB_CSS + ensureControlTabStyles  … 스타일 주입

 *   2) resolveApiBase / apiCommand               … Kit HTTP 연결

 *   3) WebFields / ApiState 타입                 … 폼·서버 JSON 규격

 *   4) defaultForm / perScreenSnapToWebFields    … 폼 초기값·스냅샷 변환

 *   5) EpTimelinePanel / SimMonitorColumn        … 읽기 전용 하위 UI

 *   6) TbsControlTab (메인)                      … state + effect + JSX

 *

 * [사용법]

 *   import TbsControlTab from "./TbsControlTab";

 *   <div className="control-tab"><TbsControlTab autoStreamingMode /></div>

 *   또는 <TbsControlTab wrapControlTab autoStreamingMode />

 *

 * [Kit 쪽 대응]

 *   morph.tbs_control_2 / morph/tbs_control_2/kit_remote_http_bridge.py

 *

 * [API 흐름 상세 가이드]

 *   docs/TBS_Web_API_Flow_Guide.md — cmd·폴링·_run_on_main·sim_start 시퀀스 등

 */



import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";



// =============================================================================

// §1 스타일 — CSS module 없이 document.head 에 1회 주입

// =============================================================================



/**

 * control-tab 영역 전용 CSS 문자열.

 * 모든 선택자가 `.control-tab` 로 시작해 회사 페이지 전역 스타일과 충돌하지 않게 함.

 */

const CONTROL_TAB_CSS = `

.control-tab,.control-tab *{box-sizing:border-box}

.control-tab{font-family:"Segoe UI",system-ui,sans-serif;font-size:13px;color:#ddd;line-height:1.35;height:100%;overflow:auto;padding:8px;background:#151820}

.control-tab .tbs-inner{max-width:840px;margin:0 auto}

.control-tab .tbs-banner{padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:12px}

.control-tab .tbs-banner-warn{background:#3d2e1a;color:#f5d4a0}

.control-tab .tbs-banner-ok{background:#1a2d24;color:#a8e6c8}

.control-tab .tbs-section{background:#1e2530;border:1px solid #3a3a3a;border-radius:8px;padding:12px;margin-bottom:12px}

.control-tab .tbs-section h2{margin:0 0 10px;font-size:15px;font-weight:600;color:#bfe7ff}

.control-tab .tbs-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:8px}

.control-tab .tbs-row label{min-width:100px;color:#888;font-size:12px}

.control-tab .tbs-narrow{min-width:36px!important}

.control-tab input[type=text],.control-tab input[type=number],.control-tab select{background:#3b4250;color:#fff;border:1px solid #5a6570;border-radius:4px;padding:4px 8px;min-height:28px}

.control-tab input[type=number]{width:72px}

.control-tab .tbs-w-path{flex:1;min-width:200px}

.control-tab button{background:#3d4a5c;color:#fff;border:1px solid #5a6b7d;border-radius:4px;padding:6px 14px;cursor:pointer;min-height:28px}

.control-tab button:hover{background:#4a5a70}

.control-tab button:disabled{opacity:.45;cursor:not-allowed}

.control-tab .tbs-check-row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}

.control-tab .tbs-check-row label{display:flex;align-items:center;gap:4px;min-width:auto;color:#ddd}

.control-tab .tbs-hint{margin:4px 0 8px;color:#888;font-size:12px}

.control-tab .tbs-port-grid{display:flex;flex-direction:column;gap:6px}

.control-tab .tbs-port-row{display:flex;gap:6px;flex-wrap:wrap}

.control-tab .tbs-port-cell{width:90px;height:26px;display:flex;align-items:center;justify-content:center;font-size:12px;border:1px solid #7b8799;border-radius:4px;background:#2a2f38}

.control-tab .tbs-port-cell-full{background:#6b5b2a}

.control-tab .tbs-port-cell-lot{background:#1f4a36}

.control-tab .tbs-log-panel{border:1px solid #5a6570;border-radius:4px;background:#0d0f12;padding:8px;min-height:100px;max-height:160px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-family:Consolas,"Courier New",monospace;font-size:11px;color:#e8e8e8}

.control-tab .tbs-hidden{display:none!important}

.control-tab .tbs-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}

.control-tab .tbs-status-line{font-size:12px;color:#888;margin-top:6px}

.control-tab .tbs-sep{height:2px;background:#3a3a3a;margin:12px 0}

.control-tab .tbs-log-title{margin:10px 0 4px;color:#888;font-size:12px}

.control-tab .tbs-port-header{margin:10px 0 6px;color:#bfe7ff;font-size:13px}

.control-tab .tbs-footer-note{font-size:12px;color:#888;margin-top:8px}

.control-tab .tbs-split-row{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-bottom:10px}

.control-tab .tbs-split-lbl{display:flex;align-items:center;gap:6px;color:#ddd;font-size:13px}

.control-tab .tbs-sim-columns{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px}

.control-tab .tbs-sim-column{flex:1 1 320px;min-width:280px;max-width:520px;border:1px solid #4a5568;border-radius:8px;padding:10px;background:#171b22}

.control-tab .tbs-port-header-sm{margin:0 0 8px;color:#bfe7ff;font-size:12px}

.control-tab .tbs-log-panel-sm{border:1px solid #5a6570;border-radius:4px;background:#0d0f12;padding:6px;min-height:72px;max-height:120px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-family:Consolas,monospace;font-size:10px;color:#e8e8e8}

.control-tab .tbs-ep-timeline{margin:10px 0;padding:6px 0;border-top:1px solid #333;border-bottom:1px solid #333}

.control-tab .tbs-ep-tick-row{display:flex;align-items:center;margin-bottom:6px}

.control-tab .tbs-ep-ticks{display:flex;justify-content:space-between}

.control-tab .tbs-ep-tick-lbl{font-size:10px;color:#bfc7d5;text-align:center}

.control-tab .tbs-ep-row{display:flex;align-items:center;gap:6px;margin-bottom:6px}

.control-tab .tbs-ep-name{font-size:11px;color:#bfc7d5;flex-shrink:0}

.control-tab .tbs-ep-bar-outer{background:#1a1e26;border-radius:2px;overflow:hidden}

.control-tab .tbs-ep-bar-track{display:flex;flex-direction:row;height:14px;width:100%}

.control-tab .tbs-ep-seg-e{background:#e53935;flex-shrink:0}

.control-tab .tbs-ep-seg-f{background:#2ecc71;flex-shrink:0}

.control-tab .tbs-ep-seg-sp{flex:1 1 auto;background:transparent}

.control-tab .tbs-ep-acc{width:52px;font-size:11px;color:#ddd;text-align:right;flex-shrink:0}

.control-tab .tbs-gate-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px}

.control-tab .tbs-gate-modal{background:#23262b;border:1px solid #5a6a80;border-radius:10px;max-width:640px;width:100%;max-height:90vh;overflow:auto;padding:16px}

.control-tab .tbs-gate-title{margin:0 0 10px;font-size:16px;color:#bfe7ff}

.control-tab .tbs-gate-pre{white-space:pre-wrap;word-break:break-word;font-family:Consolas,monospace;font-size:11px;color:#e8e8e8;background:#0d0f12;padding:10px;border-radius:6px;max-height:50vh;overflow:auto}

.control-tab .tbs-gate-ok{margin-top:12px}

`;



/** 스타일이 이미 주입됐는지 추적 (React Strict Mode 이중 마운트 대비) */

let _stylesInjected = false;



/**

 * ensureControlTabStyles

 * ─────────────────────

 * 브라우저 `<head>` 에 `<style data-tbs-control-tab>` 태그를 1회만 추가.

 * SSR 환경(document 없음)에서는 아무 것도 하지 않음.

 */

function ensureControlTabStyles(): void {

  if (_stylesInjected || typeof document === "undefined") return;

  const el = document.createElement("style");

  el.setAttribute("data-tbs-control-tab", "1");

  el.textContent = CONTROL_TAB_CSS;

  document.head.appendChild(el);

  _stylesInjected = true;

}



// =============================================================================

// §2 상수 · API 헬퍼

// =============================================================================



/** GET /api/state 폴링 주기(ms). Kit UI는 이벤트 기반이지만 웹은 주기적으로 당겨옴 */

const POLL_MS = 400;



/** 단일 화면 포트 그리드 표시 순서 (Kit 제어창과 동일 배치) */

const PORT_ORDER = ["BP1", "BP2", "BP3", "BP4", "INOUT", "EP1", "EP2", "EP3"] as const;

const PORT_ORDER_CH = PORT_ORDER;



/**

 * resolveApiBase

 * ─────────────

 * Kit HTTP 브리지의 베이스 URL 결정 (끝 슬래시 제거).

 *

 * 우선순위:

 *   1) props.apiBase (TbsControlTab 에서 직접 지정)

 *   2) Vite env VITE_TBS_KIT_API_BASE

 *   3) window.TBS_KIT_REMOTE_API (HTML에서 전역 주입)

 *   4) 기본 http://127.0.0.1:8720

 */

function resolveApiBase(override?: string): string {

  if (override !== undefined && override !== null) return String(override).replace(/\/$/, "");

  try {

    const im = import.meta as unknown as { env?: Record<string, string | undefined> };

    const v = im.env?.VITE_TBS_KIT_API_BASE;

    if (v !== undefined && v !== null) return String(v).replace(/\/$/, "");

  } catch {

    /* Vite가 아닌 번들 환경 */

  }

  if (typeof window !== "undefined") {

    const w = window as Window & { TBS_KIT_REMOTE_API?: string };

    if (w.TBS_KIT_REMOTE_API) return w.TBS_KIT_REMOTE_API.replace(/\/$/, "");

  }

  return "http://127.0.0.1:8720";

}



/** cls — 조건부 className 조합 (hidden 토글 등) */

function cls(...parts: (string | false | null | undefined)[]): string {

  return parts.filter(Boolean).join(" ");

}



/**

 * portCellClass

 * ─────────────

 * 포트 칸 배경색 class 결정.

 *   FULL  → 노란 계열 (tbs-port-cell-full)

 *   LOT 등 → 녹색 계열 (tbs-port-cell-lot)

 *   EMPTY/- → 기본 회색

 */

function portCellClass(v: string): string {

  const u = v.toUpperCase();

  if (u === "FULL") return "tbs-port-cell tbs-port-cell-full";

  if (v && v !== "-" && u !== "EMPTY") return "tbs-port-cell tbs-port-cell-lot";

  return "tbs-port-cell";

}



// =============================================================================

// §3 타입 정의 — Kit 브리지 JSON 과 1:1 대응

// =============================================================================



/**

 * WebFields

 * ─────────

 * 웹 폼 → POST /api/command 의 fields 객체.

 * Kit 쪽 kit_remote_http_bridge._apply_web_fields(ext, f) 가

 * ext._sim_*_model 등 omni.ui 모델에 복사함.

 *

 * sim_start / xml_ok / apply_fields / save_sim_screen 직전에 전송.

 */

export type WebFields = {

  lot_count: number;

  ep_count_index: number; // 0=EP2개, 1=EP3개

  lot_spawn_min: number;

  lot_spawn_max: number;

  pickup_min: number;

  pickup_max: number;

  foup_proc_min: number;

  foup_proc_max: number;

  speed: number;

  log_interval: number;

  confirm_each: boolean;

  process_time_priority: boolean;

  init_inout: boolean;

  init_bp1: boolean;

  init_bp2: boolean;

  init_bp3: boolean;

  init_bp4: boolean;

  init_ep1: boolean;

  init_ep2: boolean;

  init_ep3: boolean;

  fault_inout: boolean;

  fault_bp1: boolean;

  fault_bp2: boolean;

  fault_bp3: boolean;

  fault_bp4: boolean;

  fault_ep1: boolean;

  fault_ep2: boolean;

  fault_ep3: boolean;

  oht_min: number;

  oht_max: number;

  bp1_bp_min: number;

  bp1_bp_max: number;

  bp_ep_min: number;

  bp_ep_max: number;

  ep_oht_min: number;

  ep_oht_max: number;

  priority_prefix: string;

  xml_seq_index: number;

  xml_from: number;

  xml_to: number;

  xml_port_id: number;

  usd_path: string;

  resource_index: number;

};



/** EP 타임라인 막대의 한 구간 (빨강=empty, 초록=점유) */

type EpSeg = { empty?: unknown; dur: number };



/** GET /api/state → ep_timeline / channels[].ep_timeline */

type EpTimelineSnap = {

  t_now?: number;

  total_est: number;

  rows: Record<string, EpSeg[]>;

  empty_acc: Record<string, number>;

  row_order: string[];

};



/** 멀티 뷰포트(2~4분할) 시 화면별 모니터 컬럼 하나 */

type ChannelSnap = {

  screen: number;

  port_header: string;

  ports: Partial<Record<string, string>>;

  ep3_visible?: boolean;

  bp4_visible?: boolean;

  progress: string;

  history: string;

  ep_timeline: EpTimelineSnap;

};



/** 화면1~4에 저장된 시뮬 설정 스냅샷 (save_sim_screen / per_screen_snapshots) */

type PerScreenSnap = Record<string, unknown> | null;



/**

 * ApiState

 * ────────

 * GET /api/state 응답 전체.

 * Kit 제어창 라벨·포트셀 텍스트를 _snapshot() 이 읽어 JSON 으로 내려줌.

 * (시뮬 엔진을 웹이 직접 읽지 않음 — Kit UI 가 이미 갱신한 결과만 표시)

 */

type ApiState = {

  usd_status?: string;

  progress?: string;

  history?: string;

  port_header?: string;

  ports?: Partial<Record<string, string>>;

  ep3_visible?: boolean;

  bp4_visible?: boolean;

  kit_app?: string;

  kit_chrome_hidden?: boolean;

  viewport_split_count?: number;

  sim_multi_split_row_visible?: boolean;

  channels?: ChannelSnap[];

  ep_timeline?: EpTimelineSnap;

  per_screen_snapshots?: PerScreenSnap[];

  gate_pending?: { title?: string; message?: string } | null;

};



/** GET /api/resources → items[] 한 항목 */

type ResourceItem = { name?: string; path?: string };



/**

 * defaultForm

 * ───────────

 * 마운트 시 form state 초기값. Kit 제어창 build_control_window() 기본값과 맞춤.

 */

function defaultForm(): WebFields {

  return {

    lot_count: 6,

    ep_count_index: 0,

    lot_spawn_min: 15,

    lot_spawn_max: 40,

    pickup_min: 50,

    pickup_max: 70,

    foup_proc_min: 30,

    foup_proc_max: 60,

    speed: 1,

    log_interval: 1,

    confirm_each: false,

    process_time_priority: false,

    init_inout: false,

    init_bp1: false,

    init_bp2: false,

    init_bp3: false,

    init_bp4: false,

    init_ep1: false,

    init_ep2: false,

    init_ep3: false,

    fault_inout: false,

    fault_bp1: false,

    fault_bp2: false,

    fault_bp3: false,

    fault_bp4: false,

    fault_ep1: false,

    fault_ep2: false,

    fault_ep3: false,

    oht_min: 5,

    oht_max: 10,

    bp1_bp_min: 5,

    bp1_bp_max: 10,

    bp_ep_min: 5,

    bp_ep_max: 10,

    ep_oht_min: 5,

    ep_oht_max: 10,

    priority_prefix: "",

    xml_seq_index: 0,

    xml_from: 1,

    xml_to: 6,

    xml_port_id: 1,

    usd_path: "",

    resource_index: 0,

  };

}



/**

 * epSegIsEmpty

 * ────────────

 * Kit JSON 의 empty 플래그를 boolean 으로 해석.

 * 문자열 "false" 가 JS truthy 로 남는 경우를 방지 (막대 색상 오류 방지).

 */

function epSegIsEmpty(s: { empty?: unknown }): boolean {

  const v = s.empty;

  if (v === true || v === 1) return true;

  if (v === false || v === 0 || v === null || v === undefined) return false;

  if (typeof v === "string") {

    const t = v.trim().toLowerCase();

    return t === "true" || t === "1" || t === "yes";

  }

  return false;

}



/**

 * perScreenSnapToWebFields

 * ────────────────────────

 * 「화면N 불러오기」 시 Kit per_screen_snapshots dict → WebFields 부분 변환.

 * Kit 저장 키(spawn_min, pue_min, oht_bp1_min …)와 웹 fields 키 이름이 다름.

 */

function perScreenSnapToWebFields(s: Record<string, unknown>): Partial<WebFields> {

  const g = (k: string, d: number) => {

    const v = s[k];

    return typeof v === "number" && !Number.isNaN(v) ? v : d;

  };

  const gb = (k: string) => Boolean(s[k]);

  const gi = (k: string, d: number) => {

    const v = s[k];

    return typeof v === "number" ? Math.trunc(v) : d;

  };

  const epi = gi("ep_count_idx", 0);

  return {

    lot_count: Math.max(1, gi("lot_count", 6)),

    ep_count_index: epi >= 1 ? 1 : 0,

    lot_spawn_min: g("spawn_min", 15),

    lot_spawn_max: g("spawn_max", 40),

    pickup_min: g("pue_min", 50),

    pickup_max: g("pue_max", 70),

    foup_proc_min: g("foup_proc_min", 30),

    foup_proc_max: g("foup_proc_max", 60),

    init_inout: gb("init_inout"),

    init_bp1: gb("init_bp1"),

    init_bp2: gb("init_bp2"),

    init_bp3: gb("init_bp3"),

    init_bp4: gb("init_bp4"),

    init_ep1: gb("init_ep1"),

    init_ep2: gb("init_ep2"),

    init_ep3: gb("init_ep3"),

    fault_inout: gb("fault_inout"),

    fault_bp1: gb("fault_bp1"),

    fault_bp2: gb("fault_bp2"),

    fault_bp3: gb("fault_bp3"),

    fault_bp4: gb("fault_bp4"),

    fault_ep1: gb("fault_ep1"),

    fault_ep2: gb("fault_ep2"),

    fault_ep3: gb("fault_ep3"),

    oht_min: g("oht_bp1_min", 5),

    oht_max: g("oht_bp1_max", 10),

    bp1_bp_min: g("bp1_bp_min", 5),

    bp1_bp_max: g("bp1_bp_max", 10),

    bp_ep_min: g("bp_ep_min", 5),

    bp_ep_max: g("bp_ep_max", 10),

    ep_oht_min: g("ep_oht_min", 5),

    ep_oht_max: g("ep_oht_max", 10),

  };

}



/** mergeSnapIntoWebForm — 스냅샷 병합 시 USD/XML 등 웹 전용 필드는 prev 유지 */

function mergeSnapIntoWebForm(prev: WebFields, s: Record<string, unknown>): WebFields {

  return { ...prev, ...perScreenSnapToWebFields(s) };

}



// =============================================================================

// §4 읽기 전용 하위 컴포넌트 (GET /api/state 데이터만 표시)

// =============================================================================



/**

 * EpTimelinePanel

 * ───────────────

 * EP1/EP2/ALL_EP 행별 가로 막대 그래프 (빨강=EMPTY, 초록=LOT/FULL).

 * 데이터: snapshot.ep_timeline 또는 channels[].ep_timeline

 * Kit control_window 의 EP 점유 타임라인과 동일 의미.

 */

function EpTimelinePanel({ tl }: { tl: EpTimelineSnap }) {

  const BAR_W = 420;

  const BAR_H = 14;

  const NAME_W = 64;

  const total = Math.max(0.01, Number(tl.total_est) || 30);

  const tickStep = Math.max(10, Math.floor((total / 8 + 9.999) / 10) * 10);

  const nTicks = Math.max(1, Math.floor(total / tickStep));

  const tickLabels: number[] = [];

  for (let i = 0; i <= nTicks; i++) tickLabels.push(Math.round(i * tickStep));

  const rowOrder = Array.isArray(tl.row_order) && tl.row_order.length ? tl.row_order : ["ALL_EP", "EP1", "EP2"];



  /** renderBar — 한 EP 행의 세그먼트 너비를 total_est 비율로 계산해 div 조각으로 렌더 */

  const renderBar = (rowKey: string) => {

    const segs = (tl.rows && tl.rows[rowKey]) || [];

    let used = 0;

    const parts: React.ReactNode[] = [];

    for (let idx = 0; idx < segs.length; idx++) {

      const seg = segs[idx];

      const dur = Number(seg.dur) || 0;

      if (dur <= 1e-9) continue;

      let w = Math.round((dur / total) * BAR_W);

      w = Math.max(1, w);

      if (used + w > BAR_W) w = Math.max(1, BAR_W - used);

      used += w;

      parts.push(

        <div

          key={`${rowKey}-${idx}`}

          className={epSegIsEmpty(seg) ? "tbs-ep-seg-e" : "tbs-ep-seg-f"}

          style={{ width: w, minWidth: 1, height: BAR_H }}

        />,

      );

      if (used >= BAR_W) break;

    }

    if (used < BAR_W) parts.push(<div key={`${rowKey}-sp`} className="tbs-ep-seg-sp" style={{ width: BAR_W - used }} />);

    const acc = tl.empty_acc && typeof tl.empty_acc[rowKey] === "number" ? tl.empty_acc[rowKey] : 0;

    return (

      <div className="tbs-ep-row" key={rowKey}>

        <div className="tbs-ep-name" style={{ width: NAME_W }}>

          {rowKey}

        </div>

        <div className="tbs-ep-bar-outer" style={{ width: BAR_W, height: BAR_H }}>

          <div className="tbs-ep-bar-track">{parts}</div>

        </div>

        <div className="tbs-ep-acc">{acc.toFixed(1)}s</div>

      </div>

    );

  };



  return (

    <div className="tbs-ep-timeline">

      <div className="tbs-ep-tick-row">

        <div style={{ width: NAME_W }} />

        <div className="tbs-ep-ticks" style={{ width: BAR_W }}>

          {tickLabels.map((t, i) => (

            <span key={i} className="tbs-ep-tick-lbl" style={{ flex: 1 }}>

              {t}

            </span>

          ))}

        </div>

      </div>

      {rowOrder.map((r) => renderBar(r))}

    </div>

  );

}



/**

 * SimMonitorColumn

 * ────────────────

 * 뷰포트 2~4분할 시 화면별 세로 컬럼 하나.

 * 포트 그리드 + EP 타임라인 + 진행/이력 (channels[] 항목 1개 = 이 컴포넌트 1개)

 */

function SimMonitorColumn({ ch }: { ch: ChannelSnap }) {

  const ep3 = ch.ep3_visible !== false;

  const bp4 = ch.bp4_visible !== false;

  const cells = PORT_ORDER_CH.map((name) => {

    const raw = ch.ports && ch.ports[name] != null ? String(ch.ports[name]) : "-";

    const prefix = name === "INOUT" ? "IN/OUT" : name;

    return (

      <div key={name} className={portCellClass(raw)}>

        {prefix}:{raw}

      </div>

    );

  });

  return (

    <div className="tbs-sim-column">

      <p className="tbs-port-header-sm">{ch.port_header || `[포트·화면${ch.screen}]`}</p>

      <div className="tbs-port-grid">

        <div className="tbs-port-row">

          {cells.slice(0, 3)}

          <div className={bp4 ? undefined : "tbs-hidden"}>{cells[3]}</div>

        </div>

        <div className="tbs-port-row">

          {cells.slice(4, 7)}

          <div className={ep3 ? undefined : "tbs-hidden"}>{cells[7]}</div>

        </div>

      </div>

      {ch.ep_timeline ? <EpTimelinePanel tl={ch.ep_timeline} /> : null}

      <p className="tbs-log-title">진행현황·화면{ch.screen}</p>

      <div className="tbs-log-panel-sm">{ch.progress || ""}</div>

      <p className="tbs-log-title">이력·화면{ch.screen}</p>

      <div className="tbs-log-panel-sm">{ch.history || ""}</div>

    </div>

  );

}



// =============================================================================

// §5 Props · 메인 컴포넌트

// =============================================================================



/** TbsControlTab 에서 받는 optional props */

export type TbsControlTabProps = {

  /** Kit HTTP 브리지 베이스 URL (미지정: env / window.TBS_KIT_REMOTE_API / :8720) */

  apiBase?: string;

  /** true: 루트에 class="control-tab" 포함 (false: 부모 div.control-tab 가 래퍼) */

  wrapControlTab?: boolean;

  /** 마운트 시 kit_chrome_hide + ui_windows hide (스트리밍 배포용) */

  autoStreamingMode?: boolean;

  className?: string;

};



/**

 * TbsControlTab — 메인 export

 * ═══════════════════════════

 *

 * [상태(state) 요약]

 *   form      … 사용자 입력 (WebFields). 버튼 클릭 시 fields 로 Kit 에 전송

 *   snapshot  … GET /api/state 최신 결과 (포트·로그·gate 등 읽기 전용)

 *   resources … GET /api/resources USD 샘플 목록

 *   banner    … Kit 연결 성공/실패 메시지

 *   busy      … API 호출 중 버튼 비활성화

 *   chromeHide / hideKitTbsWindows … Kit UI 토글 체크박스 동기

 *

 * [ref]

 *   formRef   … setState 직후·클릭 직전에도 최신 form 을 collectFields() 로 읽기 위함

 */

export default function TbsControlTab({

  apiBase: apiBaseProp,

  wrapControlTab = false,

  autoStreamingMode = false,

  className,

}: TbsControlTabProps) {

  ensureControlTabStyles();



  // ── API 클라이언트 (컴포넌트 생명주기 동안 apiBase 고정) ──

  const apiBase = useMemo(() => resolveApiBase(apiBaseProp), [apiBaseProp]);



  /** apiUrl — 베이스 + "/api/state" 등 경로 조합 */

  const apiUrl = useCallback((path: string) => `${apiBase}${path}`, [apiBase]);



  /**

   * apiCommand

   * ──────────

   * POST /api/command — 모든 Kit 원격 명령의 공통 진입점.

   * body 예: { cmd: "sim_start", fields: {...} }

   * Kit 브리지가 HTTP 스레드 → Kit 메인 스레드로 dispatch.

   */

  const apiCommand = useCallback(

    async (body: Record<string, unknown>) => {

      const r = await fetch(apiUrl("/api/command"), {

        method: "POST",

        headers: { "Content-Type": "application/json" },

        body: JSON.stringify(body),

      });

      const t = await r.text();

      let j: Record<string, unknown> | null = null;

      try {

        j = JSON.parse(t) as Record<string, unknown>;

      } catch {

        /* empty */

      }

      if (!r.ok) throw new Error((j && (j.error as string)) || t || r.statusText);

      return j;

    },

    [apiUrl],

  );



  // ── React state ──

  const [form, setForm] = useState<WebFields>(defaultForm);

  const formRef = useRef(form);

  useEffect(() => {

    formRef.current = form;

  }, [form]);



  const [snapshot, setSnapshot] = useState<ApiState>({});

  const [banner, setBanner] = useState({ msg: "상태 확인 중…", ok: false });

  const [resources, setResources] = useState<ResourceItem[]>([]);

  const [busy, setBusy] = useState(false);

  const [chromeHide, setChromeHide] = useState(false);

  const [hideKitTbsWindows, setHideKitTbsWindows] = useState(false);

  const streamingInitDone = useRef(false);



  /** collectFields — POST 시점의 최신 입력값 (stale closure 방지용 formRef) */

  const collectFields = useCallback((): WebFields => ({ ...formRef.current }), []);



  /**

   * runCmd

   * ──────

   * 버튼 핸들러 래퍼: busy 플래그 + fetch 실패 시 alert.

   * 모든 POST 명령은 runCmd(() => apiCommand(...)) 패턴으로 호출.

   */

  const runCmd = useCallback(async (fn: () => Promise<unknown>) => {

    setBusy(true);

    try {

      await fn();

    } catch (e) {

      alert(e instanceof Error ? e.message : String(e));

    } finally {

      setBusy(false);

    }

  }, []);



  /** setField — form 한 필드 갱신 + formRef 동기 */

  const setField = useCallback(<K extends keyof WebFields>(key: K, value: WebFields[K]) => {

    setForm((f) => {

      const next = { ...f, [key]: value };

      formRef.current = next;

      return next;

    });

  }, []);



  /**

   * EP 개수 변경 effect

   * ───────────────────

   * ep_count_index 변경 시 Kit 에 즉시 apply_fields → on_sim_ep_count_changed

   * → 포트 그리드 EP3/BP4 표시가 /api/state 폴링으로 웹에 반영됨.

   * (첫 마운트 1회는 스킵)

   */

  const epAppliedOnce = useRef(false);

  useEffect(() => {

    if (!epAppliedOnce.current) {

      epAppliedOnce.current = true;

      return;

    }

    if (busy) return;

    runCmd(() => apiCommand({ cmd: "apply_fields", fields: collectFields() }));

  }, [form.ep_count_index, busy, runCmd, apiCommand, collectFields]);



  /**

   * loadResources

   * ─────────────

   * GET /api/resources — USD 샘플 콤보 채움.

   * 실패해도 경로 직접 입력·Load 가능.

   */

  const loadResources = useCallback(async () => {

    try {

      const r = await fetch(apiUrl("/api/resources"));

      if (!r.ok) return;

      const data = (await r.json()) as { items?: ResourceItem[] };

      setResources(data.items || []);

    } catch {

      /* 무시 */

    }

  }, [apiUrl]);



  /**

   * pollState

   * ─────────

   * GET /api/state — 400ms 마다 반복 (useEffect setInterval).

   * snapshot 갱신 → 포트 칸·진행/이력·gate 모달·분할 UI 표시/숨김.

   */

  const pollState = useCallback(async () => {

    try {

      const r = await fetch(apiUrl("/api/state"));

      if (!r.ok) throw new Error(r.statusText);

      const s = (await r.json()) as ApiState;

      setSnapshot(s);

      if (typeof s.kit_chrome_hidden === "boolean") setChromeHide(s.kit_chrome_hidden);

      setBanner({ msg: `Kit에 연결됨 — ${s.kit_app || "OK"}`, ok: true });

    } catch (e) {

      const msg = e instanceof Error ? e.message : String(e);

      setBanner({

        msg: `Kit 브리지 연결 실패. Kit 실행·프록시·apiBase 확인. (${msg})`,

        ok: false,

      });

    }

  }, [apiUrl]);



  /** 마운트: resources 1회 + state 폴링 시작 / 언마운트: interval 정리 */

  useEffect(() => {

    loadResources();

    pollState();

    const id = window.setInterval(pollState, POLL_MS);

    return () => window.clearInterval(id);

  }, [loadResources, pollState]);



  /**

   * autoStreamingMode effect

   * ────────────────────────

   * 스트리밍 페이지 기동 시 Kit 데스크톱 UI 정리:

   *   kit_chrome_hide → Kit 메뉴/패널 숨김

   *   ui_windows hide → TBS 제어창·시퀀스 편집기 omni.ui Window 숨김

   */

  useEffect(() => {

    if (!autoStreamingMode || streamingInitDone.current) return;

    streamingInitDone.current = true;

    (async () => {

      try {

        await apiCommand({ cmd: "kit_chrome_hide", hidden: true });

        await apiCommand({ cmd: "ui_windows", hide: true });

        setChromeHide(true);

        setHideKitTbsWindows(true);

      } catch {

        /* 체크박스로 수동 재시도 가능 */

      }

    })();

  }, [autoStreamingMode, apiCommand]);



  /** USD 샘플 콤보 변경 — resource_index + 해당 path 를 form 에 반영 */

  const onResourceChange = (resourceIndex: number) => {

    setField("resource_index", resourceIndex);

    if (resourceIndex <= 0) return;

    const it = resources[resourceIndex - 1];

    if (it?.path) setField("usd_path", it.path);

  };



  /** cmd:kit_chrome_hide — 실패 시 체크박스 롤백 */

  const handleChromeHideChange = async (hidden: boolean) => {

    setChromeHide(hidden);

    try {

      await apiCommand({ cmd: "kit_chrome_hide", hidden });

    } catch (e) {

      setChromeHide(!hidden);

      throw e;

    }

  };



  /** cmd:ui_windows — TBS/시퀀스 Kit 창 visible 토글 */

  const handleKitTbsWindowsHideChange = async (hide: boolean) => {

    setHideKitTbsWindows(hide);

    try {

      await apiCommand({ cmd: "ui_windows", hide });

    } catch (e) {

      setHideKitTbsWindows(!hide);

      throw e;

    }

  };



  // ── 파생 값 (snapshot + form → UI 조건) ──

  const xmlUseAb = form.xml_seq_index >= 2 && form.xml_seq_index <= 4; // FROM/TO 입력 표시

  const xmlUsePort = !xmlUseAb; // PORT_ID 입력 표시

  const splitN = Math.max(1, Math.min(4, Number(snapshot.viewport_split_count) || 1));

  const channels = useMemo((): ChannelSnap[] | null => {

    const ch = snapshot.channels;

    return Array.isArray(ch) && ch.length > 0 ? (ch as ChannelSnap[]) : null;

  }, [snapshot.channels]);

  const snapSlots = useMemo(() => {

    const a = snapshot.per_screen_snapshots;

    if (Array.isArray(a) && a.length >= 4) return a.slice(0, 4) as PerScreenSnap[];

    return [null, null, null, null] as PerScreenSnap[];

  }, [snapshot.per_screen_snapshots]);

  const hideLegacyProgressHistory = Boolean(channels && channels.length > 0); // 멀티 컬럼이면 하단 단일 로그 숨김

  const ep3Port = snapshot.ep3_visible !== false; // Kit state 기준 EP3 칸 표시

  const bp4Port = snapshot.bp4_visible !== false;

  const ep3Enabled = form.ep_count_index !== 0; // EP3개 선택 시 BP4/EP3 체크박스 표시



  /** portCells — 단일 화면 모드 포트 그리드 React 노드 배열 */

  const portCells = useMemo(() => {

    const ports = snapshot.ports || {};

    return PORT_ORDER.map((name) => {

      const v = ports[name] != null ? String(ports[name]) : "-";

      const prefix = name === "INOUT" ? "IN/OUT" : name;

      return (

        <div key={name} className={portCellClass(v)}>

          {prefix}:{v}

        </div>

      );

    });

  }, [snapshot.ports]);



  // ===========================================================================

  // §6 JSX — panel (실제 control-tab 내용)

  // ===========================================================================

  const panel = (

    <div className="tbs-inner">

      {/* ── Gate 모달: confirm_each 체크 시 Kit 이 gate_pending 을 채움 ── */}

      {snapshot.gate_pending ? (

        <div className="tbs-gate-overlay">

          <div className="tbs-gate-modal">

            <h3 className="tbs-gate-title">{snapshot.gate_pending.title || "공정 확인"}</h3>

            <pre className="tbs-gate-pre">{snapshot.gate_pending.message || ""}</pre>

            <button type="button" className="tbs-gate-ok" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "gate_confirm" }))}>

              확인

            </button>

          </div>

        </div>

      ) : null}



      {/* ── 연결 상태 배너 (pollState 성공/실패) ── */}

      <div className={cls("tbs-banner", banner.ok ? "tbs-banner-ok" : "tbs-banner-warn")}>{banner.msg}</div>



      {/* ── §6.1 화면 — cmd: kit_chrome_hide, ui_windows ── */}

      <section className="tbs-section">

        <h2>화면</h2>

        <div className="tbs-row">

          <label htmlFor="tbs_chrome_hide">기본 메뉴·패널 숨기기 (3D 뷰·TBS·시퀀스 편집기 유지)</label>

          <input

            id="tbs_chrome_hide"

            type="checkbox"

            checked={chromeHide}

            disabled={busy}

            onChange={(e) => runCmd(() => handleChromeHideChange(e.target.checked))}

          />

        </div>

        <div className="tbs-row">

          <label htmlFor="tbs_hide_kit_windows">스트리밍용: Kit의 TBS 창 숨기기</label>

          <input

            id="tbs_hide_kit_windows"

            type="checkbox"

            checked={hideKitTbsWindows}

            disabled={busy}

            onChange={(e) => runCmd(() => handleKitTbsWindowsHideChange(e.target.checked))}

          />

        </div>

      </section>



      {/* ── §6.2 뷰포트 분할 — cmd: sim_viewport_split, save_sim_screen, apply_per_screen_snapshot ── */}

      <section className="tbs-section">

        <h2>시뮼 뷰포트 분할</h2>

        {snapshot.sim_multi_split_row_visible === true ? (

          <>

            <div className="tbs-split-row">

              {[1, 2, 3, 4].map((n) => (

                <label key={n} className="tbs-split-lbl">

                  <input

                    type="radio"

                    name="tbs_split"

                    checked={splitN === n}

                    disabled={busy}

                    onChange={() => runCmd(() => apiCommand({ cmd: "sim_viewport_split", count: n }))}

                  />

                  {n}화면

                </label>

              ))}

            </div>

            <div className="tbs-row">

              {[1, 2, 3, 4]

                .filter((n) => n <= splitN)

                .map((n) => (

                  <button

                    key={`save-${n}`}

                    type="button"

                    disabled={busy}

                    onClick={() =>

                      runCmd(async () => {

                        await apiCommand({ cmd: "apply_fields", fields: collectFields() });

                        await apiCommand({ cmd: "save_sim_screen", screen: n });

                      })

                    }

                  >

                    화면{n}에 설정 저장

                  </button>

                ))}

            </div>

            <div className="tbs-row">

              {[1, 2, 3, 4].map((n) => {

                const slot = snapSlots[n - 1];

                if (!slot || typeof slot !== "object") return null;

                return (

                  <button

                    key={`load-${n}`}

                    type="button"

                    disabled={busy}

                    onClick={() =>

                      runCmd(async () => {

                        const sn = slot as Record<string, unknown>;

                        setForm((prev) => mergeSnapIntoWebForm(prev, sn));

                        await apiCommand({ cmd: "apply_per_screen_snapshot", snapshot: sn });

                      })

                    }

                  >

                    화면{n} 불러오기

                  </button>

                );

              })}

            </div>

          </>

        ) : (

          <p className="tbs-hint">USD 스테이지를 로드하면 분할·화면별 설정이 표시됩니다.</p>

        )}

      </section>



      {/* ── §6.3 USD Load — GET /api/resources, cmd: load_usd, state: usd_status ── */}

      <section className="tbs-section">

        <h2>USD Load</h2>

        <div className="tbs-row">

          <label htmlFor="tbs_resource">샘플</label>

          <select id="tbs_resource" style={{ flex: 1, minWidth: 200 }} value={String(form.resource_index)} onChange={(e) => onResourceChange(parseInt(e.target.value, 10) || 0)}>

            <option value="0">선택안함</option>

            {resources.map((it, i) => (

              <option key={i} value={String(i + 1)}>

                {it.name || it.path || String(i)}

              </option>

            ))}

          </select>

        </div>

        <div className="tbs-row">

          <label htmlFor="tbs_usd_path">경로</label>

          <input id="tbs_usd_path" className="tbs-w-path" type="text" value={form.usd_path} onChange={(e) => setField("usd_path", e.target.value)} />

        </div>

        <div className="tbs-toolbar">

          <button

            type="button"

            disabled={busy}

            onClick={() =>

              runCmd(() =>

                apiCommand({

                  cmd: "load_usd",

                  path: form.usd_path.trim(),

                  resource_index: form.resource_index,

                }),

              )

            }

          >

            Load

          </button>

        </div>

        <div className="tbs-status-line">{snapshot.usd_status || ""}</div>

      </section>



      {/* ── §6.4 XML 제너레이터 — cmd: xml_ok, xml_run ── */}

      <section className="tbs-section">

        <h2>XML 제너레이터</h2>

        <div className="tbs-row">

          <label htmlFor="tbs_xml_seq">시퀀스</label>

          <select id="tbs_xml_seq" style={{ flex: 1 }} value={form.xml_seq_index} onChange={(e) => setField("xml_seq_index", parseInt(e.target.value, 10) || 0)}>

            <option value="0">EAPEIS_PORT_READYTOLOAD</option>

            <option value="1">EAPEIS_PORT_ARRIVED</option>

            <option value="2">EAPEIS_PORT_MOVE_TRANSFERING</option>

            <option value="3">EAPEIS_PORT_MOVE</option>

            <option value="4">EISEAP_PORT_MOVE_REQ</option>

            <option value="5">EAPEIS_PORT_READYTOUNLOAD</option>

            <option value="6">EAPEIS_PORT_REMOVED</option>

          </select>

        </div>

        <div className={cls("tbs-row", !xmlUseAb && "tbs-hidden")}>

          <label>FROM / TO</label>

          <input type="number" min={1} value={form.xml_from} onChange={(e) => setField("xml_from", parseInt(e.target.value, 10) || 1)} />

          <span className="tbs-narrow">~</span>

          <input type="number" min={1} value={form.xml_to} onChange={(e) => setField("xml_to", parseInt(e.target.value, 10) || 1)} />

        </div>

        <div className={cls("tbs-row", !xmlUsePort && "tbs-hidden")}>

          <label htmlFor="tbs_xml_port">PORT_ID</label>

          <input id="tbs_xml_port" type="number" min={1} value={form.xml_port_id} onChange={(e) => setField("xml_port_id", parseInt(e.target.value, 10) || 1)} />

        </div>

        <p className="tbs-footer-note">포트 ID: EP1~3=1~3, IN/OUT=5, BP1~4=6~9, OHT=10</p>

        <div className="tbs-toolbar">

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "xml_ok", fields: collectFields() }))}>

            OK

          </button>

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "xml_run" }))}>

            제너레이터 실행(역파싱)

          </button>

        </div>

      </section>



      <div className="tbs-sep" />



      {/* ── §6.5 시뮬레이션 — cmd: sim_start/stop/reset, copy_progress, apply_fields ── */}

      {/*     읽기: snapshot.ports, progress, history, channels, ep_timeline          */}

      <section className="tbs-section">

        <h2>시뮬레이션 (simpy)</h2>

        <div className="tbs-row">

          <label htmlFor="tbs_lot">LOT 수</label>

          <input id="tbs_lot" type="number" min={1} value={form.lot_count} onChange={(e) => setField("lot_count", parseInt(e.target.value, 10) || 1)} />

          <label htmlFor="tbs_ep">EP 개수</label>

          <select id="tbs_ep" value={form.ep_count_index} onChange={(e) => setField("ep_count_index", parseInt(e.target.value, 10) || 0)}>

            <option value="0">2</option>

            <option value="1">3</option>

          </select>

        </div>

        <div className="tbs-row">

          <label>LOT생성간격</label>

          <input type="number" step={0.1} value={form.lot_spawn_min} onChange={(e) => setField("lot_spawn_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.lot_spawn_max} onChange={(e) => setField("lot_spawn_max", parseFloat(e.target.value) || 0)} />

          <label>회수간격</label>

          <input type="number" step={0.1} value={form.pickup_min} onChange={(e) => setField("pickup_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.pickup_max} onChange={(e) => setField("pickup_max", parseFloat(e.target.value) || 0)} />

        </div>

        <div className="tbs-row">

          <label>FOUP공정(EP)</label>

          <input type="number" step={0.1} value={form.foup_proc_min} onChange={(e) => setField("foup_proc_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.foup_proc_max} onChange={(e) => setField("foup_proc_max", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">초</span>

        </div>

        <p className="tbs-hint">초기 LOT 적재 포트 (체크 시 시작 시점에 FULL)</p>

        <div className="tbs-check-row">

          <label>

            <input type="checkbox" checked={form.init_inout} onChange={(e) => setField("init_inout", e.target.checked)} /> IN/OUT

          </label>

          <label>

            <input type="checkbox" checked={form.init_bp1} onChange={(e) => setField("init_bp1", e.target.checked)} /> BP1

          </label>

          <label>

            <input type="checkbox" checked={form.init_bp2} onChange={(e) => setField("init_bp2", e.target.checked)} /> BP2

          </label>

          <label>

            <input type="checkbox" checked={form.init_bp3} onChange={(e) => setField("init_bp3", e.target.checked)} /> BP3

          </label>

          <label className={ep3Enabled ? undefined : "tbs-hidden"}>

            <input type="checkbox" checked={form.init_bp4} onChange={(e) => setField("init_bp4", e.target.checked)} /> BP4

          </label>

        </div>

        <div className="tbs-check-row">

          <label>

            <input type="checkbox" checked={form.init_ep1} onChange={(e) => setField("init_ep1", e.target.checked)} /> EP1

          </label>

          <label>

            <input type="checkbox" checked={form.init_ep2} onChange={(e) => setField("init_ep2", e.target.checked)} /> EP2

          </label>

          <label className={ep3Enabled ? undefined : "tbs-hidden"}>

            <input type="checkbox" checked={form.init_ep3} onChange={(e) => setField("init_ep3", e.target.checked)} /> EP3

          </label>

        </div>

        <p className="tbs-hint">고장(비가동) 포트 (체크 시 라우팅 제외, 실행 중 즉시 반영)</p>

        <div className="tbs-check-row">

          <label>

            <input type="checkbox" checked={form.fault_inout} onChange={(e) => setField("fault_inout", e.target.checked)} /> IN/OUT

          </label>

          <label>

            <input type="checkbox" checked={form.fault_bp1} onChange={(e) => setField("fault_bp1", e.target.checked)} /> BP1

          </label>

          <label>

            <input type="checkbox" checked={form.fault_bp2} onChange={(e) => setField("fault_bp2", e.target.checked)} /> BP2

          </label>

          <label>

            <input type="checkbox" checked={form.fault_bp3} onChange={(e) => setField("fault_bp3", e.target.checked)} /> BP3

          </label>

          <label className={ep3Enabled ? undefined : "tbs-hidden"}>

            <input type="checkbox" checked={form.fault_bp4} onChange={(e) => setField("fault_bp4", e.target.checked)} /> BP4

          </label>

        </div>

        <div className="tbs-check-row">

          <label>

            <input type="checkbox" checked={form.fault_ep1} onChange={(e) => setField("fault_ep1", e.target.checked)} /> EP1

          </label>

          <label>

            <input type="checkbox" checked={form.fault_ep2} onChange={(e) => setField("fault_ep2", e.target.checked)} /> EP2

          </label>

          <label className={ep3Enabled ? undefined : "tbs-hidden"}>

            <input type="checkbox" checked={form.fault_ep3} onChange={(e) => setField("fault_ep3", e.target.checked)} /> EP3

          </label>

        </div>

        <div className="tbs-row">

          <label>OHT→IN/OUT/EP</label>

          <input type="number" step={0.1} value={form.oht_min} onChange={(e) => setField("oht_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.oht_max} onChange={(e) => setField("oht_max", parseFloat(e.target.value) || 0)} />

          <label>IN/OUT→BP</label>

          <input type="number" step={0.1} value={form.bp1_bp_min} onChange={(e) => setField("bp1_bp_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.bp1_bp_max} onChange={(e) => setField("bp1_bp_max", parseFloat(e.target.value) || 0)} />

        </div>

        <div className="tbs-row">

          <label>BP→EP</label>

          <input type="number" step={0.1} value={form.bp_ep_min} onChange={(e) => setField("bp_ep_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.bp_ep_max} onChange={(e) => setField("bp_ep_max", parseFloat(e.target.value) || 0)} />

          <label>EP→OHT</label>

          <input type="number" step={0.1} value={form.ep_oht_min} onChange={(e) => setField("ep_oht_min", parseFloat(e.target.value) || 0)} />

          <span className="tbs-narrow">~</span>

          <input type="number" step={0.1} value={form.ep_oht_max} onChange={(e) => setField("ep_oht_max", parseFloat(e.target.value) || 0)} />

        </div>

        <div className="tbs-row">

          <label htmlFor="tbs_speed">시뮬 속도배율</label>

          <input id="tbs_speed" type="number" step={0.1} min={0.1} value={form.speed} onChange={(e) => setField("speed", parseFloat(e.target.value) || 0.1)} />

          <label htmlFor="tbs_log_iv">로그주기(s)</label>

          <input id="tbs_log_iv" type="number" step={0.1} value={form.log_interval} onChange={(e) => setField("log_interval", parseFloat(e.target.value) || 0)} />

        </div>

        <div className="tbs-row">

          <label>

            <input type="checkbox" checked={form.process_time_priority} onChange={(e) => setField("process_time_priority", e.target.checked)} /> 공정설정 시간 우선

          </label>

          <label>

            <input type="checkbox" checked={form.confirm_each} onChange={(e) => setField("confirm_each", e.target.checked)} /> 각 공정 확인

          </label>

          <div style={{ flex: 1 }} />

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "sim_start", fields: collectFields() }))}>

            시작

          </button>

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "sim_stop" }))}>

            정지

          </button>

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "sim_reset" }))}>

            리셋

          </button>

        </div>

        <div className="tbs-row">

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "copy_progress" }))}>

            진행현황+Sim로그 복사

          </button>

        </div>



        {/* 멀티 분할: channels[] → SimMonitorColumn / 단일: portCells + EpTimelinePanel */}

        {channels && channels.length > 0 ? (

          <div className="tbs-sim-columns">

            {channels.map((c) => (

              <SimMonitorColumn key={c.screen} ch={c} />

            ))}

          </div>

        ) : (

          <>

            <p className="tbs-port-header">{snapshot.port_header || "[포트상태]"}</p>

            <div className="tbs-port-grid">

              <div className="tbs-port-row">

                {portCells.slice(0, 3)}

                <div className={bp4Port ? undefined : "tbs-hidden"}>{portCells[3]}</div>

              </div>

              <div className="tbs-port-row">

                {portCells.slice(4, 7)}

                <div className={ep3Port ? undefined : "tbs-hidden"}>{portCells[7]}</div>

              </div>

            </div>

            {snapshot.ep_timeline ? <EpTimelinePanel tl={snapshot.ep_timeline} /> : null}

          </>

        )}



        {!hideLegacyProgressHistory ? (

          <>

            <p className="tbs-log-title">진행현황</p>

            <div className="tbs-log-panel">{snapshot.progress || ""}</div>

            <p className="tbs-log-title">이력로그</p>

            <div className="tbs-log-panel">{snapshot.history || ""}</div>

          </>

        ) : null}

      </section>



      {/* ── §6.6 장비 prim — cmd: apply_fields(priority_prefix), prim_refresh ── */}

      <section className="tbs-section">

        <h2>장비 prim</h2>

        <div className="tbs-row">

          <label htmlFor="tbs_priority">우선 표시 접두사</label>

          <input

            id="tbs_priority"

            className="tbs-w-path"

            type="text"

            placeholder="비우면 순서대로"

            value={form.priority_prefix}

            onChange={(e) => setField("priority_prefix", e.target.value)}

            onBlur={() => runCmd(() => apiCommand({ cmd: "apply_fields", fields: collectFields() }))}

          />

        </div>

        <div className="tbs-toolbar">

          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "prim_refresh" }))}>

            목록 새로고침

          </button>

        </div>

        <p className="tbs-footer-note">prim 드롭다운 목록은 Kit에서 갱신됩니다. 스트리밍 모드에서는 Kit 창을 숨긴 경우 Viewport에서 확인하세요.</p>

      </section>

    </div>

  );



  // ── §7 래퍼: wrapControlTab 이면 control-tab class 포함 ──

  if (wrapControlTab) {

    return (

      <div className={cls("control-tab", className)}>

        {panel}

      </div>

    );

  }

  return <div className={className}>{panel}</div>;

}



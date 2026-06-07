/**
 * TBS 제어창 — 스트리밍용 React 패널 (복사용)
 *
 * 배치: 회사 Kit 스트리밍 템플릿에서
 *   src/pages/Home/components/TbsSimulation.tsx
 *   Home/index.tsx 에서 import TbsSimulation from "./components/TbsSimulation" 후 <TbsSimulation />
 *
 * 연결: morph.tbs_control_1 의 kit_remote_http_bridge.py 와 동일 HTTP 계약
 *   GET  /api/state
 *   GET  /api/resources
 *   POST /api/command  { cmd, ... }  — kit_chrome_hide: { hidden: boolean }
 *   추가 cmd: sim_viewport_split { count:1-4 }, save_sim_screen { screen:1-4 },
 *   apply_per_screen_snapshot { snapshot:{...} }, gate_confirm {}
 *
 * Vite(5173) + Kit 브리지(8720) 동시 사용:
 *   1) vite.config.ts 에서 /api 를 http://127.0.0.1:8720 으로 프록시 (vite.config.snippet.txt 참고)
 *   2) .env 에 VITE_TBS_KIT_API_BASE= 를 비우거나 생략 → 같은 오리진(5173)으로 /api 호출
 *   또는 프록시 없이 직접 Kit에 붙을 때: .env 에 VITE_TBS_KIT_API_BASE=http://127.0.0.1:8720
 *
 * 이 저장소에서는 React 빌드가 없어 여기서는 실행되지 않을 수 있음(회사 프로젝트에 붙여 넣어 사용).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./TbsSimulation.module.css";

// ---------------------------------------------------------------------------
// API 베이스 (tbs_panel.js 의 API_BASE 와 동일 역할)
// ---------------------------------------------------------------------------

function getKitApiBase(): string {
  try {
    const im = import.meta as unknown as { env?: Record<string, string | undefined> };
    const v = im.env?.VITE_TBS_KIT_API_BASE;
    if (v !== undefined && v !== null) return String(v);
  } catch {
    /* non-Vite 번들 */
  }
  if (typeof window !== "undefined") {
    const w = window as Window & { TBS_KIT_REMOTE_API?: string };
    if (w.TBS_KIT_REMOTE_API) return w.TBS_KIT_REMOTE_API;
  }
  // 프록시/환경변수/전역 주입이 없으면 기본 브리지 포트로 fallback
  // (Kit 브리지 기본값: 127.0.0.1:8720)
  return "http://127.0.0.1:8720";
}

const POLL_MS = 400;

const PORT_ORDER = ["BP1", "BP2", "BP3", "BP4", "INOUT", "EP1", "EP2", "EP3"] as const;

// ---------------------------------------------------------------------------
// 타입 (kit_remote_http_bridge._apply_web_fields / _snapshot)
// ---------------------------------------------------------------------------

export type WebFields = {
  lot_count: number;
  ep_count_index: number;
  lot_spawn_min: number;
  lot_spawn_max: number;
  pickup_min: number;
  pickup_max: number;
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

export type EpSeg = { empty: boolean; dur: number };

export type EpTimelineSnap = {
  t_now: number;
  total_est: number;
  rows: Record<string, EpSeg[]>;
  empty_acc: Record<string, number>;
  row_order: string[];
};

export type ChannelSnap = {
  screen: number;
  port_header: string;
  ports: Partial<Record<string, string>>;
  ep3_visible?: boolean;
  bp4_visible?: boolean;
  progress: string;
  history: string;
  ep_timeline: EpTimelineSnap;
};

export type PerScreenSnap = Record<string, unknown> | null;

type ApiState = {
  usd_status?: string;
  sim_line?: string;
  progress?: string;
  history?: string;
  port_header?: string;
  ports?: Partial<Record<string, string>>;
  ep3_visible?: boolean;
  bp4_visible?: boolean;
  kit_app?: string;
  /** Kit 기본 메뉴·패널 숨김 (제어창「화면」체크박스와 동기) */
  kit_chrome_hidden?: boolean;
  viewport_split_count?: number;
  sim_multi_split_row_visible?: boolean;
  channels?: ChannelSnap[];
  /** 화면1 EP 막대 — channels 가 비어도(USD 미로드 등) 시뮬 중 웹 표시용 */
  ep_timeline?: EpTimelineSnap;
  per_screen_snapshots?: PerScreenSnap[];
  gate_pending?: {
    title?: string;
    message?: string;
    gate_seq_raw?: string;
    gate_seq_canonical?: string;
    gate_xml_sequence_name?: string;
  } | null;
};

type ResourceItem = { name?: string; path?: string };

function defaultForm(): WebFields {
  return {
    lot_count: 6,
    ep_count_index: 0,
    lot_spawn_min: 15,
    lot_spawn_max: 40,
    pickup_min: 50,
    pickup_max: 70,
    speed: 1,
    log_interval: 0,
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

// ---------------------------------------------------------------------------

function apiUrl(path: string): string {
  const base = getKitApiBase().replace(/\/$/, "");
  return `${base}${path}`;
}

async function apiCommand(body: Record<string, unknown>): Promise<Record<string, unknown> | null> {
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
  if (!r.ok) {
    throw new Error((j && (j.error as string)) || t || r.statusText);
  }
  return j;
}

function portCellClass(v: string): string {
  const u = v.toUpperCase();
  let extra = "";
  if (u === "FULL") extra = ` ${styles.portCellFull}`;
  else if (v && v !== "-" && u !== "EMPTY") extra = ` ${styles.portCellLot}`;
  return `${styles.portCell}${extra}`;
}

const PORT_ORDER_CH = ["BP1", "BP2", "BP3", "BP4", "INOUT", "EP1", "EP2", "EP3"] as const;

function perScreenSnapToWebFields(s: Record<string, unknown>): WebFields {
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
    speed: 1,
    log_interval: 0,
    confirm_each: false,
    process_time_priority: false,
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
    priority_prefix: "",
    xml_seq_index: 0,
    xml_from: 1,
    xml_to: 6,
    xml_port_id: 1,
    usd_path: "",
    resource_index: 0,
  };
}

/** Kit 스냅샷 empty 플래그 — 문자열 "false" 가 truthy 로 남는 경우 방지 */
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

function mergeSnapIntoWebForm(prev: WebFields, s: Record<string, unknown>): WebFields {
  const m = perScreenSnapToWebFields(s);
  return {
    ...m,
    speed: prev.speed,
    log_interval: prev.log_interval,
    confirm_each: prev.confirm_each,
    process_time_priority: prev.process_time_priority,
    priority_prefix: prev.priority_prefix,
    xml_seq_index: prev.xml_seq_index,
    xml_from: prev.xml_from,
    xml_to: prev.xml_to,
    xml_port_id: prev.xml_port_id,
    usd_path: prev.usd_path,
    resource_index: prev.resource_index,
  };
}

function EpTimelinePanel({ tl }: { tl: EpTimelineSnap }) {
  const BAR_W = 420;
  const BAR_H = 14;
  const NAME_W = 64;
  const total = Math.max(0.01, Number(tl.total_est) || 30);
  const tickStep = Math.max(10, Math.floor((total / 8 + 9.999) / 10) * 10);
  const nTicks = Math.max(1, Math.floor(total / tickStep));
  const tickLabels: number[] = [];
  for (let i = 0; i <= nTicks; i++) tickLabels.push(Math.round(i * tickStep));
  const rowOrder = Array.isArray(tl.row_order) && tl.row_order.length ? tl.row_order : ["EP1", "EP2", "ALL_EP"];

  const renderBar = (rowKey: string) => {
    const segs = (tl.rows && tl.rows[rowKey]) || [];
    let used = 0;
    const parts: React.ReactNode[] = [];
    for (let idx = 0; idx < segs.length; idx++) {
      const s = segs[idx];
      const dur = Number(s.dur) || 0;
      if (dur <= 1e-9) continue;
      let w = Math.round((dur / total) * BAR_W);
      w = Math.max(1, w);
      if (used + w > BAR_W) w = Math.max(1, BAR_W - used);
      used += w;
      parts.push(
        <div
          key={`${rowKey}-${idx}`}
          className={epSegIsEmpty(s) ? styles.epSegEmpty : styles.epSegFull}
          style={{ width: w, minWidth: 1, height: BAR_H }}
        />,
      );
      if (used >= BAR_W) break;
    }
    if (used < BAR_W) {
      parts.push(<div key={`${rowKey}-sp`} className={styles.epSegSpacer} style={{ width: BAR_W - used }} />);
    }
    const acc = tl.empty_acc && typeof tl.empty_acc[rowKey] === "number" ? tl.empty_acc[rowKey] : 0;
    return (
      <div className={styles.epRow} key={rowKey}>
        <div className={styles.epName} style={{ width: NAME_W }}>
          {rowKey}
        </div>
        <div className={styles.epBarOuter} style={{ width: BAR_W, height: BAR_H }}>
          <div className={styles.epBarTrack}>{parts}</div>
        </div>
        <div className={styles.epAcc}>{acc.toFixed(1)}s</div>
      </div>
    );
  };

  return (
    <div className={styles.epTimeline}>
      <div className={styles.epTickRow}>
        <div style={{ width: NAME_W }} />
        <div className={styles.epTicks} style={{ width: BAR_W }}>
          {tickLabels.map((t, i) => (
            <span key={i} className={styles.epTickLbl} style={{ flex: 1 }}>
              {t}
            </span>
          ))}
        </div>
      </div>
      {rowOrder.map((r) => renderBar(r))}
    </div>
  );
}

function SimMonitorColumn({ ch, styles: st }: { ch: ChannelSnap; styles: typeof styles }) {
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
    <div className={st.simColumn}>
      <p className={st.portHeaderSm}>{ch.port_header || `[포트·화면${ch.screen}]`}</p>
      <div className={st.portGrid}>
        <div className={st.portRow}>
          {cells.slice(0, 3)}
          <div className={bp4 ? undefined : st.hidden}>{cells[3]}</div>
        </div>
        <div className={st.portRow}>
          {cells.slice(4, 7)}
          <div className={ep3 ? undefined : st.hidden}>{cells[7]}</div>
        </div>
      </div>
      {ch.ep_timeline ? <EpTimelinePanel tl={ch.ep_timeline} /> : null}
      <p className={st.logTitle}>진행현황·화면{ch.screen}</p>
      <div className={st.logPanelSm}>{ch.progress || ""}</div>
      <p className={st.logTitle}>이력·화면{ch.screen}</p>
      <div className={st.logPanelSm}>{ch.history || ""}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TbsSimulation() {
  const [form, setForm] = useState<WebFields>(defaultForm);
  const formRef = useRef<WebFields>(form);
  useEffect(() => {
    formRef.current = form;
  }, [form]);
  // 표시모드 제거: 항상 둘다(진행현황+이력로그)
  const [snapshot, setSnapshot] = useState<ApiState>({});
  const [banner, setBanner] = useState<{ msg: string; ok: boolean }>({
    msg: "상태 확인 중…",
    ok: false,
  });
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [busy, setBusy] = useState(false);
  /** 제어창「기본 메뉴·패널 숨기기」와 동일; GET /api/state 의 kit_chrome_hidden 과 동기 */
  const [chromeHide, setChromeHide] = useState(false);
  /** 스트리밍용: Kit 내부 TBS 창 숨김 */
  const [hideKitTbsWindows, setHideKitTbsWindows] = useState(false);

  const xmlUseAb = form.xml_seq_index >= 2 && form.xml_seq_index <= 4;
  const xmlUsePort = !xmlUseAb;

  const setField = useCallback(<K extends keyof WebFields>(key: K, value: WebFields[K]) => {
    setForm((f) => {
      const next = { ...f, [key]: value };
      formRef.current = next;
      return next;
    });
  }, []);

  // 클릭 직전(렌더 커밋 전)에도 최신 입력값을 보내기 위해 ref 기준으로 수집
  const collectFields = useCallback((): WebFields => ({ ...formRef.current }), []);

  // EP 개수 변경 등 “즉시 UI 반영”이 필요한 항목은 Kit에 바로 적용한다.
  // (포트표의 EP3/BP4 가시성은 Kit 쪽 on_sim_ep_count_changed 결과를 /api/state로 받아야 동기화된다.)
  useEffect(() => {
    // 초기 마운트 직후에는 불필요한 호출을 줄이기 위해 1회 스킵
    if ((window as unknown as { __tbsAppliedOnce?: boolean }).__tbsAppliedOnce !== true) {
      (window as unknown as { __tbsAppliedOnce?: boolean }).__tbsAppliedOnce = true;
      return;
    }
    // busy 중에는 다음 변경에서 다시 적용되도록 여기서는 스킵
    if (busy) return;
    runCmd(() => apiCommand({ cmd: "apply_fields", fields: collectFields() }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.ep_count_index]);

  const loadResources = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/resources"));
      if (!r.ok) return;
      const data = (await r.json()) as { items?: ResourceItem[] };
      setResources(data.items || []);
    } catch {
      /* 콤보 없이 경로 직접 입력 가능 */
    }
  }, []);

  const pollState = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/state"));
      if (!r.ok) throw new Error(r.statusText);
      const s = (await r.json()) as ApiState;
      setSnapshot(s);
      if (typeof s.kit_chrome_hidden === "boolean") {
        setChromeHide(s.kit_chrome_hidden);
      }
      setBanner({
        msg: `Kit에 연결됨 — ${s.kit_app || "OK"}`,
        ok: true,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setBanner({
        msg:
          "Kit 브리지에 연결할 수 없습니다. Kit 실행·브리지·프록시(또는 VITE_TBS_KIT_API_BASE)를 확인하세요. (" +
          msg +
          ")",
        ok: false,
      });
    }
  }, []);

  useEffect(() => {
    loadResources();
    pollState();
    const id = window.setInterval(pollState, POLL_MS);
    return () => window.clearInterval(id);
  }, [loadResources, pollState]);

  const onResourceChange = (resourceIndex: number) => {
    setField("resource_index", resourceIndex);
    if (resourceIndex <= 0) return;
    const it = resources[resourceIndex - 1];
    if (it?.path) setField("usd_path", it.path);
  };

  const runCmd = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // 표시모드 제거: log_mode 명령/상태는 더 이상 사용하지 않음

  const handleChromeHideChange = async (hidden: boolean) => {
    setChromeHide(hidden);
    try {
      await apiCommand({ cmd: "kit_chrome_hide", hidden });
    } catch (e) {
      setChromeHide(!hidden);
      throw e;
    }
  };

  const handleKitTbsWindowsHideChange = async (hide: boolean) => {
    setHideKitTbsWindows(hide);
    try {
      await apiCommand({ cmd: "ui_windows", hide });
    } catch (e) {
      setHideKitTbsWindows(!hide);
      throw e;
    }
  };

  const showProgress = true;
  const showHistory = true;

  const splitN = Math.max(1, Math.min(4, Number(snapshot.viewport_split_count) || 1));
  const channels = useMemo((): ChannelSnap[] | null => {
    const ch = snapshot.channels;
    if (Array.isArray(ch) && ch.length > 0) return ch as ChannelSnap[];
    return null;
  }, [snapshot.channels]);
  const snapSlots = useMemo(() => {
    const a = snapshot.per_screen_snapshots;
    if (Array.isArray(a) && a.length >= 4) return a.slice(0, 4) as PerScreenSnap[];
    return [null, null, null, null] as PerScreenSnap[];
  }, [snapshot.per_screen_snapshots]);
  const hideLegacyProgressHistory = Boolean(channels && channels.length > 0);

  const ep3Port = snapshot.ep3_visible !== false;
  const bp4Port = snapshot.bp4_visible !== false;
  const ep3Enabled = form.ep_count_index !== 0;
  const showBp4 = ep3Enabled;
  const showEp3InitRow = ep3Enabled;

  const portCells = useMemo(() => {
    const ports = snapshot.ports || {};
    return PORT_ORDER.map((name) => {
      const raw = ports[name] != null ? String(ports[name]) : "-";
      const v = raw;
      const prefix = name === "INOUT" ? "IN/OUT" : name;
      const label = `${prefix}:${v}`;
      return (
        <div key={name} className={portCellClass(v)}>
          {label}
        </div>
      );
    });
  }, [snapshot.ports]);

  return (
    <div className={styles.wrap}>
      {snapshot.gate_pending ? (
        <div className={styles.gateOverlay}>
          <div className={styles.gateModal}>
            <h3 className={styles.gateTitle}>{snapshot.gate_pending.title || "공정 확인"}</h3>
            <pre className={styles.gatePre}>{snapshot.gate_pending.message || ""}</pre>
            <button
              type="button"
              className={styles.gateOk}
              disabled={busy}
              onClick={() => runCmd(() => apiCommand({ cmd: "gate_confirm" }))}
            >
              확인
            </button>
          </div>
        </div>
      ) : null}

      <div className={`${styles.banner} ${banner.ok ? styles.bannerOk : styles.bannerWarn}`}>{banner.msg}</div>

      <section className={styles.section}>
        <h2>화면</h2>
        <div className={styles.row}>
          <label htmlFor="tbs_chrome_hide">기본 메뉴·패널 숨기기 (3D 뷰·TBS·시퀀스 편집기 유지)</label>
          <input
            id="tbs_chrome_hide"
            type="checkbox"
            checked={chromeHide}
            disabled={busy}
            onChange={(e) =>
              runCmd(async () => {
                await handleChromeHideChange(e.target.checked);
              })
            }
          />
        </div>
        <div className={styles.row}>
          <label htmlFor="tbs_hide_kit_windows">스트리밍용: Kit의 TBS 창 숨기기</label>
          <input
            id="tbs_hide_kit_windows"
            type="checkbox"
            checked={hideKitTbsWindows}
            disabled={busy}
            onChange={(e) =>
              runCmd(async () => {
                await handleKitTbsWindowsHideChange(e.target.checked);
              })
            }
          />
        </div>
      </section>

      <section className={styles.section}>
        <h2>시뮼 뷰포트 분할 (Kit 제어창과 동일)</h2>
        {snapshot.sim_multi_split_row_visible === true ? (
          <>
            <div className={styles.splitRow}>
              {[1, 2, 3, 4].map((n) => (
                <label key={n} className={styles.splitLbl}>
                  <input
                    type="radio"
                    name="tbs_split"
                    checked={splitN === n}
                    disabled={busy}
                    onChange={() =>
                      runCmd(() =>
                        apiCommand({
                          cmd: "sim_viewport_split",
                          count: n,
                        }),
                      )
                    }
                  />
                  {n}화면
                </label>
              ))}
            </div>
            <div className={styles.row}>
              {[1, 2, 3, 4]
                .filter((n) => n <= splitN)
                .map((n) => (
                  <button
                    key={`save-sc-${n}`}
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
            <div className={styles.row}>
              {[1, 2, 3, 4].map((n) => {
                const slot = snapSlots[n - 1];
                if (!slot || typeof slot !== "object") return null;
                return (
                  <button
                    key={`load-sc-${n}`}
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
          <p className={styles.hint}>USD 스테이지를 로드하면 분할·화면별 설정 저장/불러오기가 표시됩니다.</p>
        )}
      </section>

      <section className={styles.section}>
        <h2>USD Load</h2>
        <div className={styles.row}>
          <label htmlFor="tbs_resource">샘플</label>
          <select
            id="tbs_resource"
            style={{ flex: 1, minWidth: 200 }}
            value={String(form.resource_index)}
            onChange={(e) => onResourceChange(parseInt(e.target.value, 10) || 0)}
          >
            <option value="0">선택안함</option>
            {resources.map((it, i) => (
              <option key={i} value={String(i + 1)}>
                {it.name || it.path || String(i)}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.row}>
          <label htmlFor="tbs_usd_path">경로</label>
          <input
            id="tbs_usd_path"
            className={styles.wPath}
            type="text"
            value={form.usd_path}
            onChange={(e) => setField("usd_path", e.target.value)}
          />
        </div>
        <div className={styles.toolbar}>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              runCmd(() =>
                apiCommand({
                  cmd: "load_usd",
                  path: form.usd_path.trim(),
                  resource_index: form.resource_index,
                })
              )
            }
          >
            Load
          </button>
        </div>
        <div className={styles.statusLine}>{snapshot.usd_status || ""}</div>
      </section>

      <section className={styles.section}>
        <h2>XML 제너레이터</h2>
        <div className={styles.row}>
          <label htmlFor="tbs_xml_seq">시퀀스</label>
          <select
            id="tbs_xml_seq"
            style={{ flex: 1 }}
            value={form.xml_seq_index}
            onChange={(e) => setField("xml_seq_index", parseInt(e.target.value, 10) || 0)}
          >
            <option value="0">EAPEIS_PORT_READYTOLOAD</option>
            <option value="1">EAPEIS_PORT_ARRIVED</option>
            <option value="2">EAPEIS_PORT_MOVE_TRANSFERING</option>
            <option value="3">EAPEIS_PORT_MOVE</option>
            <option value="4">EISEAP_PORT_MOVE_REQ</option>
            <option value="5">EAPEIS_PORT_READYTOUNLOAD</option>
            <option value="6">EAPEIS_PORT_REMOVED</option>
          </select>
        </div>
        <div className={`${styles.row} ${xmlUseAb ? "" : styles.hidden}`}>
          <label>FROM / TO</label>
          <input
            type="number"
            min={1}
            value={form.xml_from}
            onChange={(e) => setField("xml_from", parseInt(e.target.value, 10) || 1)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            min={1}
            value={form.xml_to}
            onChange={(e) => setField("xml_to", parseInt(e.target.value, 10) || 1)}
          />
        </div>
        <div className={`${styles.row} ${xmlUsePort ? "" : styles.hidden}`}>
          <label htmlFor="tbs_xml_port">PORT_ID</label>
          <input
            id="tbs_xml_port"
            type="number"
            min={1}
            value={form.xml_port_id}
            onChange={(e) => setField("xml_port_id", parseInt(e.target.value, 10) || 1)}
          />
        </div>
        <p className={styles.footerNote}>포트 ID 표: EP1~3=1~3, IN/OUT=5, BP1~4=6~9, OHT=10</p>
        <div className={styles.toolbar}>
          <button
            type="button"
            disabled={busy}
            onClick={() => runCmd(() => apiCommand({ cmd: "xml_ok", fields: collectFields() }))}
          >
            OK
          </button>
          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "xml_run" }))}>
            제너레이터 실행(역파싱)
          </button>
        </div>
      </section>

      <div className={styles.sep} />

      <section className={styles.section}>
        <h2>시뮬레이션 (simpy)</h2>
        <div className={styles.row}>
          <label htmlFor="tbs_lot">LOT 수</label>
          <input
            id="tbs_lot"
            type="number"
            min={1}
            value={form.lot_count}
            onChange={(e) => setField("lot_count", parseInt(e.target.value, 10) || 1)}
          />
          <label htmlFor="tbs_ep">EP 개수</label>
          <select
            id="tbs_ep"
            value={form.ep_count_index}
            onChange={(e) => setField("ep_count_index", parseInt(e.target.value, 10) || 0)}
          >
            <option value="0">2</option>
            <option value="1">3</option>
          </select>
        </div>
        <div className={styles.row}>
          <label>LOT생성간격</label>
          <input
            type="number"
            step={0.1}
            value={form.lot_spawn_min}
            onChange={(e) => setField("lot_spawn_min", parseFloat(e.target.value) || 0)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            step={0.1}
            value={form.lot_spawn_max}
            onChange={(e) => setField("lot_spawn_max", parseFloat(e.target.value) || 0)}
          />
          <label>회수간격</label>
          <input
            type="number"
            step={0.1}
            value={form.pickup_min}
            onChange={(e) => setField("pickup_min", parseFloat(e.target.value) || 0)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            step={0.1}
            value={form.pickup_max}
            onChange={(e) => setField("pickup_max", parseFloat(e.target.value) || 0)}
          />
        </div>
        <p className={styles.hint}>초기 LOT 적재 포트 (체크 시 시작 시점에 FULL)</p>
        <div className={styles.checkRow}>
          <label>
            <input type="checkbox" checked={form.init_inout} onChange={(e) => setField("init_inout", e.target.checked)} />{" "}
            IN/OUT
          </label>
          <label>
            <input type="checkbox" checked={form.init_bp1} onChange={(e) => setField("init_bp1", e.target.checked)} />{" "}
            BP1
          </label>
          <label>
            <input type="checkbox" checked={form.init_bp2} onChange={(e) => setField("init_bp2", e.target.checked)} />{" "}
            BP2
          </label>
          <label>
            <input type="checkbox" checked={form.init_bp3} onChange={(e) => setField("init_bp3", e.target.checked)} />{" "}
            BP3
          </label>
          <label className={showBp4 ? "" : styles.hidden}>
            <input type="checkbox" checked={form.init_bp4} onChange={(e) => setField("init_bp4", e.target.checked)} />{" "}
            BP4
          </label>
        </div>
        <div className={styles.checkRow}>
          <label>
            <input type="checkbox" checked={form.init_ep1} onChange={(e) => setField("init_ep1", e.target.checked)} />{" "}
            EP1
          </label>
          <label>
            <input type="checkbox" checked={form.init_ep2} onChange={(e) => setField("init_ep2", e.target.checked)} />{" "}
            EP2
          </label>
          <label className={showEp3InitRow ? "" : styles.hidden}>
            <input type="checkbox" checked={form.init_ep3} onChange={(e) => setField("init_ep3", e.target.checked)} /> EP3
          </label>
        </div>

        <p className={styles.hint}>고장(비가동) 포트 (체크 시 EMPTY로 간주 / 이동 불가)</p>
        <div className={styles.checkRow}>
          <label>
            <input type="checkbox" checked={form.fault_inout} onChange={(e) => setField("fault_inout", e.target.checked)} />{" "}
            IN/OUT
          </label>
          <label>
            <input type="checkbox" checked={form.fault_bp1} onChange={(e) => setField("fault_bp1", e.target.checked)} />{" "}
            BP1
          </label>
          <label>
            <input type="checkbox" checked={form.fault_bp2} onChange={(e) => setField("fault_bp2", e.target.checked)} />{" "}
            BP2
          </label>
          <label>
            <input type="checkbox" checked={form.fault_bp3} onChange={(e) => setField("fault_bp3", e.target.checked)} />{" "}
            BP3
          </label>
          <label className={showBp4 ? "" : styles.hidden}>
            <input type="checkbox" checked={form.fault_bp4} onChange={(e) => setField("fault_bp4", e.target.checked)} /> BP4
          </label>
        </div>
        <div className={styles.checkRow}>
          <label>
            <input type="checkbox" checked={form.fault_ep1} onChange={(e) => setField("fault_ep1", e.target.checked)} />{" "}
            EP1
          </label>
          <label>
            <input type="checkbox" checked={form.fault_ep2} onChange={(e) => setField("fault_ep2", e.target.checked)} />{" "}
            EP2
          </label>
          <label className={showEp3InitRow ? "" : styles.hidden}>
            <input type="checkbox" checked={form.fault_ep3} onChange={(e) => setField("fault_ep3", e.target.checked)} /> EP3
          </label>
        </div>
        <div className={styles.row}>
          <label>OHT→IN/OUT/EP</label>
          <input
            type="number"
            step={0.1}
            value={form.oht_min}
            onChange={(e) => setField("oht_min", parseFloat(e.target.value) || 0)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            step={0.1}
            value={form.oht_max}
            onChange={(e) => setField("oht_max", parseFloat(e.target.value) || 0)}
          />
          <label>IN/OUT→BP</label>
          <input
            type="number"
            step={0.1}
            value={form.bp1_bp_min}
            onChange={(e) => setField("bp1_bp_min", parseFloat(e.target.value) || 0)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            step={0.1}
            value={form.bp1_bp_max}
            onChange={(e) => setField("bp1_bp_max", parseFloat(e.target.value) || 0)}
          />
        </div>
        <div className={styles.row}>
          <label>BP→EP</label>
          <input
            type="number"
            step={0.1}
            value={form.bp_ep_min}
            onChange={(e) => setField("bp_ep_min", parseFloat(e.target.value) || 0)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            step={0.1}
            value={form.bp_ep_max}
            onChange={(e) => setField("bp_ep_max", parseFloat(e.target.value) || 0)}
          />
          <label>EP→OHT</label>
          <input
            type="number"
            step={0.1}
            value={form.ep_oht_min}
            onChange={(e) => setField("ep_oht_min", parseFloat(e.target.value) || 0)}
          />
          <span className={styles.narrow}>~</span>
          <input
            type="number"
            step={0.1}
            value={form.ep_oht_max}
            onChange={(e) => setField("ep_oht_max", parseFloat(e.target.value) || 0)}
          />
        </div>
        <div className={styles.row}>
          <label htmlFor="tbs_speed">시뮬 속도배율</label>
          <input
            id="tbs_speed"
            type="number"
            step={0.1}
            min={0.1}
            value={form.speed}
            onChange={(e) => setField("speed", parseFloat(e.target.value) || 0.1)}
          />
          <label htmlFor="tbs_log_iv">로그주기(s)</label>
          <input
            id="tbs_log_iv"
            type="number"
            step={0.1}
            value={form.log_interval}
            onChange={(e) => setField("log_interval", parseFloat(e.target.value) || 0)}
          />
        </div>
        <div className={styles.row}>
          <label>
            <input
              type="checkbox"
              checked={form.process_time_priority}
              onChange={(e) => setField("process_time_priority", e.target.checked)}
            />{" "}
            공정설정 시간 우선
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.confirm_each}
              onChange={(e) => setField("confirm_each", e.target.checked)}
            />{" "}
            각 공정 확인
          </label>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              runCmd(async () => {
                // 시작은 항상 fields 포함: Kit 쪽에서 "시작 시점"에 동일 payload로 세팅/초기화가 보장되게 한다.
                await apiCommand({ cmd: "sim_start", fields: collectFields() });
              })
            }
          >
            시작
          </button>
          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "sim_stop" }))}>
            정지
          </button>
          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "sim_reset" }))}>
            리셋
          </button>
        </div>
        <div className={styles.row}>
          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "copy_progress" }))}>
            진행현황+Sim로그 복사
          </button>
        </div>

        {channels && channels.length > 0 ? (
          <div className={styles.simColumns}>
            {channels.map((c) => (
              <SimMonitorColumn key={c.screen} ch={c} styles={styles} />
            ))}
          </div>
        ) : (
          <>
            <div id="tbs_panelPort">
              <p className={styles.portHeader}>{snapshot.port_header || "[포트상태]"}</p>
              <div className={styles.portGrid}>
                <div className={styles.portRow}>
                  {portCells.slice(0, 3)}
                  <div className={bp4Port ? undefined : styles.hidden}>{portCells[3]}</div>
                </div>
                <div className={styles.portRow}>
                  {portCells.slice(4, 7)}
                  <div className={ep3Port ? undefined : styles.hidden}>{portCells[7]}</div>
                </div>
              </div>
            </div>
            {snapshot.ep_timeline ? <EpTimelinePanel tl={snapshot.ep_timeline} /> : null}
          </>
        )}

        <div className={showProgress && !hideLegacyProgressHistory ? "" : styles.hidden}>
          <p className={styles.logTitle}>진행현황</p>
          <div className={styles.logPanel}>{snapshot.progress || ""}</div>
        </div>
        <div className={showHistory && !hideLegacyProgressHistory ? "" : styles.hidden}>
          <p className={styles.logTitle}>이력로그</p>
          <div className={styles.logPanel}>{snapshot.history || ""}</div>
        </div>
      </section>
    </div>
  );
}

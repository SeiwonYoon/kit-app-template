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
 *
 * Vite(5173) + Kit 브리지(8720) 동시 사용:
 *   1) vite.config.ts 에서 /api 를 http://127.0.0.1:8720 으로 프록시 (vite.config.snippet.txt 참고)
 *   2) .env 에 VITE_TBS_KIT_API_BASE= 를 비우거나 생략 → 같은 오리진(5173)으로 /api 호출
 *   또는 프록시 없이 직접 Kit에 붙을 때: .env 에 VITE_TBS_KIT_API_BASE=http://127.0.0.1:8720
 *
 * 이 저장소에서는 React 빌드가 없어 여기서는 실행되지 않을 수 있음(회사 프로젝트에 붙여 넣어 사용).
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
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
  return "";
}

const POLL_MS = 400;

const PORT_ORDER = ["BP2", "BP3", "BP4", "BP1", "EP1", "EP2", "EP3"] as const;

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
  init_bp1: boolean;
  init_bp2: boolean;
  init_bp3: boolean;
  init_bp4: boolean;
  init_ep1: boolean;
  init_ep2: boolean;
  init_ep3: boolean;
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

type ApiState = {
  usd_status?: string;
  sim_line?: string;
  progress?: string;
  history?: string;
  port_header?: string;
  ports?: Partial<Record<string, string>>;
  ep3_visible?: boolean;
  kit_app?: string;
  /** Kit 기본 메뉴·패널 숨김 (제어창「화면」체크박스와 동기) */
  kit_chrome_hidden?: boolean;
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
    init_bp1: false,
    init_bp2: false,
    init_bp3: false,
    init_bp4: false,
    init_ep1: false,
    init_ep2: false,
    init_ep3: false,
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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TbsSimulation() {
  const [form, setForm] = useState<WebFields>(defaultForm);
  const [logMode, setLogMode] = useState(0);
  const [snapshot, setSnapshot] = useState<ApiState>({});
  const [banner, setBanner] = useState<{ msg: string; ok: boolean }>({
    msg: "상태 확인 중…",
    ok: false,
  });
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [busy, setBusy] = useState(false);
  /** 제어창「기본 메뉴·패널 숨기기」와 동일; GET /api/state 의 kit_chrome_hidden 과 동기 */
  const [chromeHide, setChromeHide] = useState(false);

  const xmlUseAb = form.xml_seq_index >= 2 && form.xml_seq_index <= 4;
  const xmlUsePort = !xmlUseAb;
  const showEp3InitRow = form.ep_count_index === 1;

  const setField = useCallback(<K extends keyof WebFields>(key: K, value: WebFields[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
  }, []);

  const collectFields = useCallback((): WebFields => ({ ...form }), [form]);

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

  const handleLogModeChange = async (index: number) => {
    setLogMode(index);
    try {
      await apiCommand({ cmd: "log_mode", index });
    } catch (e) {
      console.warn(e);
    }
  };

  const handleChromeHideChange = async (hidden: boolean) => {
    setChromeHide(hidden);
    try {
      await apiCommand({ cmd: "kit_chrome_hide", hidden });
    } catch (e) {
      setChromeHide(!hidden);
      throw e;
    }
  };

  const showProgress = logMode === 0 || logMode === 1;
  const showHistory = logMode === 0 || logMode === 2;

  const ep3Port = snapshot.ep3_visible !== false;

  const portCells = useMemo(() => {
    const ports = snapshot.ports || {};
    return PORT_ORDER.map((name) => {
      const raw = ports[name] != null ? String(ports[name]) : "-";
      const v = raw;
      const label = `${name}:${v}`;
      return (
        <div key={name} className={portCellClass(v)}>
          {label}
        </div>
      );
    });
  }, [snapshot.ports]);

  return (
    <div className={styles.wrap}>
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
          <label>
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
        <div className={styles.row}>
          <label>OHT→BP/EP</label>
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
          <label>BP1→BP</label>
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
          <label>
            <input
              type="checkbox"
              checked={form.confirm_each}
              onChange={(e) => setField("confirm_each", e.target.checked)}
            />{" "}
            각 공정 확인
          </label>
        </div>
        <div className={styles.toolbar}>
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
        <div className={styles.row}>
          <label htmlFor="tbs_log_mode">표시모드</label>
          <select
            id="tbs_log_mode"
            value={logMode}
            onChange={(e) => handleLogModeChange(parseInt(e.target.value, 10) || 0)}
          >
            <option value="0">둘다</option>
            <option value="1">진행현황</option>
            <option value="2">이력로그</option>
          </select>
          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "copy_progress" }))}>
            진행현황 복사
          </button>
        </div>

        <div id="tbs_panelPort">
          <p className={styles.portHeader}>{snapshot.port_header || "[포트상태]"}</p>
          <div className={styles.portGrid}>
            <div className={styles.portRow}>{portCells.slice(0, 3)}</div>
            <div className={styles.portRow}>
              {portCells.slice(3, 6)}
              <div className={ep3Port ? undefined : styles.hidden}>{portCells[6]}</div>
            </div>
          </div>
        </div>

        <div className={showProgress ? "" : styles.hidden}>
          <p className={styles.logTitle}>진행현황</p>
          <div className={styles.logPanel}>{snapshot.progress || ""}</div>
        </div>
        <div className={showHistory ? "" : styles.hidden}>
          <p className={styles.logTitle}>이력로그</p>
          <div className={styles.logPanel}>{snapshot.history || ""}</div>
        </div>
        <div className={styles.statusLine}>{snapshot.sim_line || ""}</div>
      </section>

      <section className={styles.section}>
        <h2>장비 prim</h2>
        <div className={styles.row}>
          <label htmlFor="tbs_pri">우선 표시 접두사</label>
          <input
            id="tbs_pri"
            className={styles.wPath}
            type="text"
            placeholder="비우면 순서대로"
            value={form.priority_prefix}
            onChange={(e) => setField("priority_prefix", e.target.value)}
          />
        </div>
        <div className={styles.toolbar}>
          <button type="button" disabled={busy} onClick={() => runCmd(() => apiCommand({ cmd: "prim_refresh" }))}>
            목록 새로고침
          </button>
        </div>
        <p className={styles.footerNote}>
          prim 드롭다운 목록은 Kit 제어창과 동기화됩니다. 새로고침 후 Kit 창에서 확인할 수 있습니다.
        </p>
      </section>
    </div>
  );
}

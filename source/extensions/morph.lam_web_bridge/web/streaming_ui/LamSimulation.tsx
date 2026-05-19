/**
 * LAM 제어창 — 스트리밍용 React 패널 (복사용)
 *
 * 배치: 회사 Kit 스트리밍 템플릿에서
 *   src/pages/Home/components/LamSimulation.tsx
 *   <LamSimulation />
 *
 * 연결: morph.lam_web_bridge/lam_remote_http_bridge.py — GET /api/state, POST /api/command
 * Vite 프록시: vite-dev-proxy-snippet.txt 참고
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import styles from "./LamSimulation.module.css";

function getKitApiBase(): string {
  try {
    const im = import.meta as unknown as { env?: Record<string, string | undefined> };
    const v = im.env?.VITE_TBS_KIT_API_BASE;
    if (v !== undefined && v !== null) return String(v);
  } catch {
    /* non-Vite */
  }
  if (typeof window !== "undefined") {
    const w = window as Window & { TBS_KIT_REMOTE_API?: string };
    if (w.TBS_KIT_REMOTE_API) return w.TBS_KIT_REMOTE_API;
  }
  return "http://127.0.0.1:8720";
}

const POLL_MS = 400;

export type CsvFileItem = { name?: string; path?: string };

export type LamApiState = {
  log?: string;
  master_path?: string;
  instance_count?: number;
  csv_dir?: string;
  csv_selected?: string;
  csv_files?: CsvFileItem[];
  schedule?: string;
  progress?: string;
  playing?: boolean;
  building?: boolean;
};

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

function clampSpeed(v: number): number {
  return Math.max(0.1, Math.min(20, isFinite(v) ? v : 1));
}

export default function LamSimulation() {
  const [connOk, setConnOk] = useState(false);
  const [connMsg, setConnMsg] = useState("상태 확인 중…");

  const [masterPath, setMasterPath] = useState("");
  const [csvDir, setCsvDir] = useState("");
  const [csvFiles, setCsvFiles] = useState<CsvFileItem[]>([]);
  const [csvSelected, setCsvSelected] = useState("");
  const [speed, setSpeed] = useState(1);
  const [schedule, setSchedule] = useState("");
  const [progress, setProgress] = useState("");
  const [log, setLog] = useState("(대기)");
  const [instanceCount, setInstanceCount] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [building, setBuilding] = useState(false);

  const masterPathRef = useRef(masterPath);
  const csvDirRef = useRef(csvDir);
  const csvSelectedRef = useRef(csvSelected);
  const speedRef = useRef(speed);

  /** true 이면 폴링이 해당 필드를 덮어쓰지 않음 */
  const userTouchedRef = useRef({
    master: false,
    csvDir: false,
    csvFile: false,
    speed: false,
  });
  const initialSyncDoneRef = useRef(false);

  masterPathRef.current = masterPath;
  csvDirRef.current = csvDir;
  csvSelectedRef.current = csvSelected;
  speedRef.current = speed;

  const applyState = useCallback((s: LamApiState) => {
    const touched = userTouchedRef.current;
    const first = !initialSyncDoneRef.current;
    if (first) {
      initialSyncDoneRef.current = true;
    }

    if ((first || !touched.master) && s.master_path != null) {
      setMasterPath(s.master_path);
    }
    if ((first || !touched.csvDir) && s.csv_dir != null) {
      setCsvDir(s.csv_dir);
    }
    if (s.csv_files != null) {
      setCsvFiles(s.csv_files);
      if (first || !touched.csvFile) {
        if (s.csv_selected != null) setCsvSelected(s.csv_selected);
      } else if (csvSelectedRef.current) {
        const still = s.csv_files.some(
          (it) =>
            (it.path ?? "") === csvSelectedRef.current ||
            (it.name ?? "") === csvSelectedRef.current
        );
        if (!still && s.csv_files.length > 0) {
          setCsvSelected(s.csv_files[0].path ?? "");
        }
      }
    } else if ((first || !touched.csvFile) && s.csv_selected != null) {
      setCsvSelected(s.csv_selected);
    }

    if (s.schedule != null) setSchedule(s.schedule);
    if (s.progress != null) setProgress(s.progress);
    if (s.log != null) setLog(s.log);
    if (s.instance_count != null) setInstanceCount(s.instance_count);
    if (s.playing != null) setPlaying(s.playing);
    if (s.building != null) setBuilding(s.building);
  }, []);

  const pollState = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/state"));
      if (!r.ok) throw new Error(r.statusText);
      const s = (await r.json()) as LamApiState;
      applyState(s);
      const base = getKitApiBase() || "(same origin)";
      setConnOk(true);
      setConnMsg(`Kit LAM 연결됨 — ${base}`);
    } catch (e) {
      setConnOk(false);
      setConnMsg(`Kit 연결 실패 — ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [applyState]);

  const csvPayload = useCallback(
    () => ({
      csv_dir: csvDirRef.current.trim(),
      csv_path: csvSelectedRef.current,
      speed_scale: clampSpeed(speedRef.current),
    }),
    []
  );

  const refreshCsvList = useCallback(async () => {
    try {
      const keepPath = csvSelectedRef.current;
      const j = await apiCommand({
        cmd: "csv_refresh_list",
        csv_dir: csvDirRef.current.trim(),
      });
      userTouchedRef.current.csvDir = false;
      const items = (j?.items as CsvFileItem[] | undefined) ?? [];
      if (items.length) {
        const pick =
          keepPath &&
          items.some((it) => (it.path ?? "") === keepPath || (it.name ?? "") === keepPath)
            ? keepPath
            : (items[0].path ?? "");
        setCsvSelected(pick);
        userTouchedRef.current.csvFile = true;
      }
      await pollState();
    } catch (e) {
      setLog(`목록 새로고침 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [pollState]);

  const onOpenMaster = useCallback(async () => {
    try {
      await apiCommand({ cmd: "open_master", path: masterPathRef.current.trim() });
      userTouchedRef.current.master = false;
      await pollState();
    } catch (e) {
      setLog(`Open Master 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [pollState]);

  const onTimelineRefresh = useCallback(async () => {
    try {
      await apiCommand({ cmd: "csv_timeline_refresh", ...csvPayload() });
      userTouchedRef.current.csvDir = false;
      userTouchedRef.current.csvFile = false;
      await pollState();
    } catch (e) {
      setLog(`타임라인 갱신 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [csvPayload, pollState]);

  const onCsvPlay = useCallback(async () => {
    try {
      await apiCommand({ cmd: "csv_play", ...csvPayload() });
      userTouchedRef.current.csvDir = false;
      userTouchedRef.current.csvFile = false;
      await pollState();
    } catch (e) {
      setLog(`CSV Play 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [csvPayload, pollState]);

  const onCsvStop = useCallback(async () => {
    try {
      await apiCommand({ cmd: "csv_stop" });
      await pollState();
    } catch (e) {
      setLog(`CSV 중지 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [pollState]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await pollState();
      if (!cancelled && csvFiles.length === 0) {
        await refreshCsvList();
      }
    })();
    const id = window.setInterval(() => void pollState(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once
  }, [pollState, refreshCsvList]);

  const statusBadge =
    playing || building
      ? ` · ${playing ? "재생 중" : ""}${playing && building ? " / " : ""}${building ? "빌드 중" : ""}`
      : "";

  return (
    <div className={styles.wrap}>
      <div
        className={
          connOk ? `${styles.banner} ${styles.bannerOk}` : `${styles.banner} ${styles.bannerWarn}`
        }
      >
        {connMsg}
        {statusBadge}
      </div>

      <section className={styles.section}>
        <h2>합성 USD 열기</h2>
        <p className={styles.hint}>Kit LAM Window 의 Open Master 와 동일 — Discover + Extract 자동</p>
        <div className={styles.row}>
          <label htmlFor="lam-master-path">경로</label>
          <input
            id="lam-master-path"
            className={styles.pathInput}
            type="text"
            value={masterPath}
            onChange={(e) => {
              userTouchedRef.current.master = true;
              setMasterPath(e.target.value);
            }}
          />
        </div>
        <div className={styles.toolbar}>
          <button type="button" onClick={() => void onOpenMaster()}>
            Open Master
          </button>
        </div>
        <div className={styles.statusLine}>{masterPath || "(경로 없음)"}</div>
        <div className={`${styles.statusLine} ${styles.dim}`}>등록 인스턴스: {instanceCount}개</div>
      </section>

      <section className={styles.section}>
        <h2>CSV 시뮬 재생</h2>
        <div className={styles.row}>
          <label htmlFor="lam-csv-dir">CSV 폴더</label>
          <input
            id="lam-csv-dir"
            className={styles.pathInput}
            type="text"
            value={csvDir}
            onChange={(e) => {
              userTouchedRef.current.csvDir = true;
              setCsvDir(e.target.value);
            }}
          />
        </div>
        <div className={styles.row}>
          <label htmlFor="lam-csv-file">CSV 파일</label>
          <select
            id="lam-csv-file"
            className={styles.select}
            value={csvSelected}
            onChange={(e) => {
              userTouchedRef.current.csvFile = true;
              setCsvSelected(e.target.value);
            }}
          >
            {csvFiles.length === 0 ? (
              <option value="">(CSV 없음)</option>
            ) : (
              csvFiles.map((it) => (
                <option key={it.path ?? it.name} value={it.path ?? ""}>
                  {it.name ?? it.path}
                </option>
              ))
            )}
          </select>
        </div>
        <div className={styles.toolbar}>
          <button type="button" onClick={() => void refreshCsvList()}>
            목록 새로고침
          </button>
          <button type="button" onClick={() => void onTimelineRefresh()} disabled={building}>
            타임라인 갱신
          </button>
          <button type="button" onClick={() => void onCsvPlay()} disabled={playing}>
            CSV Play
          </button>
          <button type="button" onClick={() => void onCsvStop()} disabled={!playing}>
            CSV 중지
          </button>
        </div>
        <div className={styles.row}>
          <label htmlFor="lam-speed">재생 배속</label>
          <input
            id="lam-speed"
            type="number"
            step={0.1}
            min={0.1}
            max={20}
            value={speed}
            onChange={(e) => {
              userTouchedRef.current.speed = true;
              setSpeed(clampSpeed(parseFloat(e.target.value)));
            }}
          />
          <button
            type="button"
            onClick={() => {
              userTouchedRef.current.speed = true;
              setSpeed(1);
            }}
          >
            1x
          </button>
          <button
            type="button"
            onClick={() => {
              userTouchedRef.current.speed = true;
              setSpeed(5);
            }}
          >
            5x
          </button>
        </div>
        <p className={styles.hint}>CSV t까지 대기 + JSON 전 스텝 실행 (둘 다 ÷배속)</p>
        <label className={styles.blockLabel} htmlFor="lam-schedule">
          CSV 재생 타임라인
        </label>
        <textarea
          id="lam-schedule"
          className={styles.readonlyArea}
          readOnly
          rows={14}
          value={schedule}
        />
        <label className={styles.blockLabel} htmlFor="lam-progress">
          빌드·재생 진행
        </label>
        <input id="lam-progress" className={styles.readonlyLine} readOnly value={progress} />
      </section>

      <div className={`${styles.statusLine} ${styles.logLine}`}>{log}</div>
    </div>
  );
}

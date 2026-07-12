import { useCallback, useState } from "react";
import {
  type HyViewTransportMode,
  requestControlSimulation,
  requestEbsEnable,
  requestEqpChange,
  requestStartSimulation,
} from "./hyviewMessaging";

type Props = {
  messagingReady: boolean;
  transportMode: HyViewTransportMode;
  onLog: (line: string) => void;
  lastV2T: string;
};

type CaseForm = {
  epCount: number;
  ebsEnable: boolean;
  lotCount: number;
};

const defaultCase = (): CaseForm => ({
  epCount: 2,
  ebsEnable: true,
  lotCount: 6,
});

function snapshotFromForm(form: CaseForm): Record<string, unknown> {
  return {
    lot_count: form.lotCount,
    ep_count: form.epCount,
    ebs_enabled: form.ebsEnable,
  };
}

export default function EbsSimPanel({ messagingReady, transportMode, onLog, lastV2T }: Props) {
  const [case0, setCase0] = useState<CaseForm>(defaultCase);
  const [case1, setCase1] = useState<CaseForm>(defaultCase);
  const [speed, setSpeed] = useState(1.0);
  const [busy, setBusy] = useState(false);

  const guard = useCallback(() => {
    if (!messagingReady) {
      onLog(
        transportMode === "http"
          ? "[WARN] HTTP 브리지 연결 후 사용하세요 (Kit morph.editor.kit + 8721)."
          : "[WARN] 스트림 연결 후 사용하세요.",
      );
      return false;
    }
    return true;
  }, [messagingReady, transportMode, onLog]);

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      if (!guard()) {
        return;
      }
      setBusy(true);
      try {
        await fn();
        onLog(`→ ${label}`);
      } catch (err) {
        onLog(`[ERR] ${label}: ${String(err)}`);
      } finally {
        setBusy(false);
      }
    },
    [guard, onLog],
  );

  const onEqpChange = (caseIndex: number, form: CaseForm) => {
    void run(`T2V_request_eqp_change case=${caseIndex} ep_count=${form.epCount}`, () =>
      requestEqpChange(caseIndex, form.epCount),
    );
  };

  const onEbsToggle = (caseIndex: number, form: CaseForm, value: boolean) => {
    const next = { ...form, ebsEnable: value };
    if (caseIndex === 0) setCase0(next);
    else setCase1(next);
    void run(`T2V_request_ebs_enable case=${caseIndex} ebs_enable=${value}`, () =>
      requestEbsEnable(caseIndex, value),
    );
  };

  const onStart = () => {
    void run("T2V_request_start_simulation", () =>
      requestStartSimulation([snapshotFromForm(case0), snapshotFromForm(case1)]),
    );
  };

  const onPlay = () => {
    void run(`T2V_request_control_simulation play speed=${speed}`, () =>
      requestControlSimulation("play", speed),
    );
  };

  const onPause = () => {
    void run("T2V_request_control_simulation pause", () => requestControlSimulation("pause"));
  };

  const renderCase = (
    title: string,
    caseIndex: number,
    form: CaseForm,
    setForm: (f: CaseForm) => void,
  ) => (
    <section className="case-card" key={caseIndex}>
      <h3>
        {title} <small>(case {caseIndex})</small>
      </h3>
      <div className="row">
        <label>
          EP
          <select
            value={form.epCount}
            onChange={(e) => setForm({ ...form, epCount: Number(e.target.value) })}
          >
            <option value={2}>2</option>
            <option value={3}>3</option>
          </select>
        </label>
        <button type="button" onClick={() => onEqpChange(caseIndex, form)} disabled={busy}>
          EP 적용
        </button>
      </div>
      <label className="row">
        <input
          type="checkbox"
          checked={form.ebsEnable}
          onChange={(e) => onEbsToggle(caseIndex, form, e.target.checked)}
        />
        EBS 적용
      </label>
      <label className="row">
        LOT
        <input
          type="number"
          min={1}
          value={form.lotCount}
          onChange={(e) => setForm({ ...form, lotCount: Number(e.target.value) })}
        />
      </label>
    </section>
  );

  return (
    <div className="ebs-panel">
      <h2>EBS / 시뮬 제어</h2>
      <p className="hint">
        {transportMode === "http"
          ? "HTTP → carb T2V → ebs_handler (스트리밍 불필요)"
          : "Livestream → ebs_handler (실무 HyView 동일)"}
      </p>

      {renderCase("화면 1", 0, case0, setCase0)}
      {renderCase("화면 2", 1, case1, setCase1)}

      <section className="sim-actions">
        <button type="button" className="primary" onClick={onStart} disabled={!messagingReady || busy}>
          시뮬 시작
        </button>
        <label>
          배속
          <input
            type="number"
            min={0.1}
            step={0.1}
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
          />
        </label>
        <button type="button" onClick={onPlay} disabled={!messagingReady || busy}>
          Play
        </button>
        <button type="button" onClick={onPause} disabled={!messagingReady || busy}>
          Pause
        </button>
      </section>

      <section className="result-box">
        <h3>마지막 V2T</h3>
        <pre>{lastV2T || "(대기)"}</pre>
      </section>
    </div>
  );
}

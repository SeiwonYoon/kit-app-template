import { useCallback, useState } from "react";
import {
  requestControlSimulation,
  requestEbsEnable,
  requestEqpChange,
  requestStartSimulation,
} from "./hyviewMessaging";

type Props = {
  streamConnected: boolean;
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

export default function EbsSimPanel({ streamConnected, onLog, lastV2T }: Props) {
  const [case0, setCase0] = useState<CaseForm>(defaultCase);
  const [case1, setCase1] = useState<CaseForm>(defaultCase);
  const [speed, setSpeed] = useState(1.0);

  const guard = useCallback(() => {
    if (!streamConnected) {
      onLog("[WARN] 스트림 연결 후 사용하세요.");
      return false;
    }
    return true;
  }, [streamConnected, onLog]);

  const onEqpChange = (caseIndex: number, form: CaseForm) => {
    if (!guard()) return;
    requestEqpChange(caseIndex, form.epCount);
    onLog(`→ T2V_request_eqp_change case=${caseIndex} ep_count=${form.epCount}`);
  };

  const onEbsToggle = (caseIndex: number, form: CaseForm, value: boolean) => {
    const next = { ...form, ebsEnable: value };
    if (caseIndex === 0) setCase0(next);
    else setCase1(next);
    if (!guard()) return;
    requestEbsEnable(caseIndex, value);
    onLog(`→ T2V_request_ebs_enable case=${caseIndex} ebs_enable=${value}`);
  };

  const onStart = () => {
    if (!guard()) return;
    requestStartSimulation([snapshotFromForm(case0), snapshotFromForm(case1)]);
    onLog("→ T2V_request_start_simulation");
  };

  const onPlay = () => {
    if (!guard()) return;
    requestControlSimulation("play", speed);
    onLog(`→ T2V_request_control_simulation play speed=${speed}`);
  };

  const onPause = () => {
    if (!guard()) return;
    requestControlSimulation("pause");
    onLog("→ T2V_request_control_simulation pause");
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
        <button type="button" onClick={() => onEqpChange(caseIndex, form)}>
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
      <p className="hint">실무 HyView 와 동일 T2V/V2T — Kit ebs_handler 경로</p>

      {renderCase("화면 1", 0, case0, setCase0)}
      {renderCase("화면 2", 1, case1, setCase1)}

      <section className="sim-actions">
        <button type="button" className="primary" onClick={onStart} disabled={!streamConnected}>
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
        <button type="button" onClick={onPlay} disabled={!streamConnected}>
          Play
        </button>
        <button type="button" onClick={onPause} disabled={!streamConnected}>
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

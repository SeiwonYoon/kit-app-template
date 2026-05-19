/**
 * LAM Kit 원격 패널 — TBS tbs_panel.js 와 동일 연결 (TBS_REMOTE_UI_PORT, TBS_KIT_REMOTE_API).
 *
 * GET  /api/state
 * POST /api/command  { cmd: open_master | csv_refresh_list | csv_timeline_refresh | csv_play | csv_stop }
 *
 * 사용자가 편집한 경로·드롭다운·배속은 폴링으로 덮어쓰지 않음 (서버는 schedule/progress/log 만 동기).
 */
(function () {
  const API_BASE =
    (typeof window !== "undefined" && window.TBS_KIT_REMOTE_API) || "";
  const POLL_MS = 400;

  /** undefined = 서버 폴링 값 반영, 문자열/숫자 = 사용자 입력 유지 */
  const userEdits = {
    master_path: undefined,
    csv_dir: undefined,
    csv_file: undefined,
    speed: undefined,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function apiUrl(path) {
    return API_BASE + path;
  }

  function setBanner(msg, ok) {
    const el = $("connBanner");
    if (!el) return;
    el.textContent = msg;
    el.className = "banner " + (ok ? "ok" : "warn");
  }

  function readSpeed() {
    const v = parseFloat($("f_speed")?.value || "1");
    return Math.max(0.1, Math.min(20, isFinite(v) ? v : 1));
  }

  function currentCsvFilePath() {
    const sel = $("f_csv_file");
    const opt = sel?.selectedOptions?.[0];
    return (opt?.dataset?.path || opt?.value || "").trim();
  }

  function lockUserEditsFromDom() {
    const mp = $("f_master_path");
    if (mp) userEdits.master_path = mp.value;
    const cd = $("f_csv_dir");
    if (cd) userEdits.csv_dir = cd.value;
    userEdits.csv_file = currentCsvFilePath();
    userEdits.speed = readSpeed();
  }

  function releaseUserEdit(key) {
    userEdits[key] = undefined;
  }

  function csvPayload() {
    const cd = $("f_csv_dir");
    return {
      csv_dir: (
        userEdits.csv_dir !== undefined ? userEdits.csv_dir : cd?.value || ""
      ).trim(),
      csv_path:
        userEdits.csv_file !== undefined
          ? userEdits.csv_file
          : currentCsvFilePath(),
      speed_scale:
        userEdits.speed !== undefined ? userEdits.speed : readSpeed(),
    };
  }

  async function apiCommand(body) {
    const r = await fetch(apiUrl("/api/command"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const t = await r.text();
    let j = null;
    try {
      j = JSON.parse(t);
    } catch (_) {
      /* ignore */
    }
    if (!r.ok) {
      throw new Error((j && j.error) || t || r.statusText);
    }
    return j;
  }

  function fillCsvSelect(items, selectedPath) {
    const sel = $("f_csv_file");
    if (!sel) return;
    const keep =
      selectedPath !== undefined && selectedPath !== null
        ? String(selectedPath)
        : currentCsvFilePath();
    sel.innerHTML = "";
    const list = items || [];
    if (!list.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "(CSV 없음)";
      sel.appendChild(o);
      userEdits.csv_file = "";
      return;
    }
    let pick = 0;
    list.forEach((it, i) => {
      const o = document.createElement("option");
      o.value = it.path || it.name || "";
      o.dataset.path = it.path || "";
      o.textContent = it.name || it.path || "";
      if (
        keep &&
        (it.path === keep || it.name === keep || o.value === keep)
      ) {
        pick = i;
      }
      sel.appendChild(o);
    });
    sel.selectedIndex = pick;
    const chosen = sel.selectedOptions?.[0];
    userEdits.csv_file = (chosen?.dataset?.path || chosen?.value || "").trim();
  }

  function applyState(s) {
    if (!s) return;

    const mp = $("f_master_path");
    if (mp) {
      if (userEdits.master_path !== undefined) {
        mp.value = userEdits.master_path;
      } else if (s.master_path) {
        mp.value = s.master_path;
      }
    }

    const cd = $("f_csv_dir");
    if (cd) {
      if (userEdits.csv_dir !== undefined) {
        cd.value = userEdits.csv_dir;
      } else if (s.csv_dir) {
        cd.value = s.csv_dir;
      }
    }

    const selPath =
      userEdits.csv_file !== undefined ? userEdits.csv_file : s.csv_selected;
    if (s.csv_files) {
      fillCsvSelect(s.csv_files, selPath);
    }

    const sp = $("f_speed");
    if (sp && userEdits.speed !== undefined) {
      sp.value = String(userEdits.speed);
    }

    const sch = $("f_schedule");
    if (sch && s.schedule != null) sch.value = s.schedule;
    const pr = $("f_progress");
    if (pr && s.progress != null) pr.value = s.progress;
    const log = $("logLine");
    if (log && s.log != null) log.textContent = s.log;
    const ic = $("instanceCount");
    if (ic) {
      ic.textContent = "등록 인스턴스: " + (s.instance_count ?? 0) + "개";
    }
    const ms = $("masterStatus");
    if (ms) {
      const show =
        userEdits.master_path !== undefined
          ? userEdits.master_path
          : s.master_path || mp?.value || "";
      ms.textContent = show;
    }
  }

  async function pollState() {
    try {
      const r = await fetch(apiUrl("/api/state"));
      if (!r.ok) throw new Error(r.statusText);
      const s = await r.json();
      applyState(s);
      setBanner("Kit LAM 연결됨 — " + API_BASE, true);
    } catch (e) {
      setBanner("Kit 연결 실패 — " + (e.message || e), false);
    }
  }

  async function refreshCsvList() {
    try {
      const keepFile = currentCsvFilePath();
      const j = await apiCommand({
        cmd: "csv_refresh_list",
        csv_dir: ($("f_csv_dir")?.value || "").trim(),
      });
      releaseUserEdit("csv_dir");
      if (j && j.items) {
        const pick =
          keepFile ||
          (j.items.find((it) => it.path === j.csv_selected)?.path) ||
          j.items[0]?.path;
        fillCsvSelect(j.items, pick);
      }
      await pollState();
    } catch (e) {
      $("logLine").textContent = "목록 새로고침 실패: " + e.message;
    }
  }

  async function onOpenMaster() {
    try {
      lockUserEditsFromDom();
      await apiCommand({
        cmd: "open_master",
        path: (userEdits.master_path || "").trim(),
      });
      releaseUserEdit("master_path");
      await pollState();
    } catch (e) {
      $("logLine").textContent = "Open Master 실패: " + e.message;
    }
  }

  async function onTimelineRefresh() {
    try {
      lockUserEditsFromDom();
      await apiCommand({
        cmd: "csv_timeline_refresh",
        ...csvPayload(),
      });
      releaseUserEdit("csv_dir");
      releaseUserEdit("csv_file");
      await pollState();
    } catch (e) {
      $("logLine").textContent = "타임라인 갱신 실패: " + e.message;
    }
  }

  async function onCsvPlay() {
    try {
      lockUserEditsFromDom();
      await apiCommand({ cmd: "csv_play", ...csvPayload() });
      releaseUserEdit("csv_dir");
      releaseUserEdit("csv_file");
      await pollState();
    } catch (e) {
      $("logLine").textContent = "CSV Play 실패: " + e.message;
    }
  }

  async function onCsvStop() {
    try {
      await apiCommand({ cmd: "csv_stop" });
      await pollState();
    } catch (e) {
      $("logLine").textContent = "CSV 중지 실패: " + e.message;
    }
  }

  function wireUserEditLocks() {
    $("f_master_path")?.addEventListener("input", () => {
      userEdits.master_path = $("f_master_path").value;
    });
    $("f_csv_dir")?.addEventListener("input", () => {
      userEdits.csv_dir = $("f_csv_dir").value;
    });
    $("f_csv_file")?.addEventListener("change", () => {
      userEdits.csv_file = currentCsvFilePath();
    });
    $("f_speed")?.addEventListener("input", () => {
      userEdits.speed = readSpeed();
    });
  }

  function wire() {
    wireUserEditLocks();
    $("btnOpenMaster")?.addEventListener("click", () => onOpenMaster());
    $("btnCsvRefresh")?.addEventListener("click", () => refreshCsvList());
    $("btnTimelineRefresh")?.addEventListener("click", () => onTimelineRefresh());
    $("btnCsvPlay")?.addEventListener("click", () => onCsvPlay());
    $("btnCsvStop")?.addEventListener("click", () => onCsvStop());
    $("btnSpeed1")?.addEventListener("click", () => {
      const el = $("f_speed");
      if (el) el.value = "1";
      userEdits.speed = 1;
    });
    $("btnSpeed5")?.addEventListener("click", () => {
      const el = $("f_speed");
      if (el) el.value = "5";
      userEdits.speed = 5;
    });
  }

  async function init() {
    wire();
    await pollState();
    await refreshCsvList();
    setInterval(pollState, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init());
  } else {
    init();
  }
})();

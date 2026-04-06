/**
 * =============================================================================
 * TBS Kit 원격 패널 (tbs_panel.js)
 * =============================================================================
 * Kit 확장 morph.tbs_control_1 의 HTTP 브리지(kit_remote_http_bridge.py)와만 통신한다.
 * 시뮬/USD 로직은 전부 Kit 프로세스 안에서 실행되고, 이 파일은 "원격 제어창" 역할만 한다.
 *
 * [Kit 쪽 대응 표]
 * ┌────────────────────┬──────────────────────────────────────────────────────┐
 * │ 이 파일에서 하는 일 │ Kit Python (브리지 → 실제 확장)                        │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ GET /api/state     │ kit_remote_http_bridge._snapshot()                   │
 * │                    │ → ext 의 라벨/포트셀 텍스트 읽기                      │
 * │                    │   (control_window 가 갱신한 진행현황·이력·포트상태)      │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ GET /api/resources │ get_resource_usd_list() (usd_loader_utils)           │
 * │                    │ = load_window.get_resource_usd_list 과 동일 소스 │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ POST cmd:load_usd  │ load_window.on_load_usd(ext) + 경로/콤보 모델 반영      │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ POST cmd:sim_start │ _apply_web_fields(ext,fields) → on_sim_start_clicked   │
 * │                    │ (control_window.py, 시뮬 엔진 TBSSimulationEngine 생성) │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ sim_stop / reset   │ on_sim_stop_clicked / on_sim_reset_clicked           │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ prim_refresh       │ refresh_object_list(ext)                             │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ log_mode           │ 콤보 모델 설정 → on_sim_log_view_changed(ext)         │
 * │                    │ (SimLogPanelMode: 둘다/진행현황만/이력만)               │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ copy_progress      │ on_copy_sim_progress(ext) (Kit 클립보드)               │
 * ├────────────────────┼──────────────────────────────────────────────────────┤
 * │ xml_ok             │ _apply_web_fields → on_xml_ok_clicked(ext)             │
 * │ xml_run            │ on_xml_run_clicked(ext)                              │
 * └────────────────────┴──────────────────────────────────────────────────────┘
 *
 * 프로토콜 요약:
 *   GET  /api/state      → UI 스냅샷 JSON
 *   GET  /api/resources → 샘플 USD 목록 { items: [{name, path}, ...] }
 *   POST /api/command   → { "cmd": "...", ... }
 */

(function () {
  // ---------------------------------------------------------------------------
  // 전역 상수 · 상태
  // ---------------------------------------------------------------------------
  /**
   * Kit 브리지 베이스 URL.
   * - 빈 문자열: 이 HTML을 브리지가 서빙하는 경우(같은 오리진) 상대 경로로 /api/... 호출.
   * - React 등 다른 포트: 로드 전 window.TBS_KIT_REMOTE_API = "http://127.0.0.1:8720"
   */
  const API_BASE = (typeof window !== "undefined" && window.TBS_KIT_REMOTE_API) || "";

  /** 진행현황·포트 등 폴링 주기(ms). Kit UI는 이벤트 기반이지만 웹은 주기적 GET 으로 맞춤. */
  const POLL_MS = 400;

  /**
   * 클라이언트만의 UI 상태 (서버와 별도).
   * - logMode: 표시모드 콤보 값 캐시 → applyLogModeVisibility 와 동기.
   * - ep3Visible: pollState 응답의 ep3_visible 과 함께 EP3 칸 표시 제어.
   */
  const state = {
    ep3Visible: true,
    logMode: 0,
  };

  // ---------------------------------------------------------------------------
  // $(id)
  // ---------------------------------------------------------------------------
  /** document.getElementById 단축. 없으면 null. */
  function $(id) {
    return document.getElementById(id);
  }

  // ---------------------------------------------------------------------------
  // setBanner(msg, ok)
  // ---------------------------------------------------------------------------
  /**
   * 상단 연결 상태 배너 갱신.
   * - Kit 연결 성공 시 pollState 에서 초록(ok), 실패 시 경고(warn).
   * Kit 대응: 없음 (순수 웹 UI).
   */
  function setBanner(msg, ok) {
    const el = $("connBanner");
    if (!el) return;
    el.textContent = msg;
    el.className = "banner " + (ok ? "ok" : "warn");
  }

  // ---------------------------------------------------------------------------
  // collectFields()
  // ---------------------------------------------------------------------------
  /**
   * index.html 의 입력 요소를 읽어 Kit 쪽 _apply_web_fields() 가 기대하는 JSON 객체로 만든다.
   * sim_start / xml_ok 전에 브리지로 전달되며, 브리지가 ext._sim_*_model 등에 복사한 뒤
   * control_window.on_sim_start_clicked / on_xml_ok_clicked 를 호출한다.
   *
   * 필드 ↔ Kit 제어창(build_control_window) 위젯 대응:
   *   lot_count ~ ep_oht_* : 시뮬레이션(simpy) 프레임의 FloatField/IntField/CheckBox
   *   priority_prefix      : "우선 표시 이름 규칙" StringField
   *   xml_*                : XML 제너레이터 콤보·FROM/TO/PORT_ID
   *   usd_path, resource_index : USD Load 경로 필드·resource 콤보 (load_usd 시 사용)
   */
  function collectFields() {
    // 로컬 헬퍼: 문자열/정수/실수/체크박스 읽기
    const g = (id) => {
      const n = $(id);
      return n ? n.value : "";
    };
    const gi = (id) => parseInt(g(id), 10) || 0;
    const gf = (id) => parseFloat(g(id)) || 0;
    const gb = (id) => !!$(id)?.checked;

    return {
      lot_count: gi("f_lot_count"),
      ep_count_index: gi("f_ep_count"),
      lot_spawn_min: gf("f_spawn_min"),
      lot_spawn_max: gf("f_spawn_max"),
      pickup_min: gf("f_pickup_min"),
      pickup_max: gf("f_pickup_max"),
      speed: gf("f_speed"),
      log_interval: gf("f_log_interval"),
      confirm_each: gb("f_confirm_each"),
      init_bp1: gb("f_init_bp1"),
      init_bp2: gb("f_init_bp2"),
      init_bp3: gb("f_init_bp3"),
      init_bp4: gb("f_init_bp4"),
      init_ep1: gb("f_init_ep1"),
      init_ep2: gb("f_init_ep2"),
      init_ep3: gb("f_init_ep3"),
      oht_min: gf("f_oht_min"),
      oht_max: gf("f_oht_max"),
      bp1_bp_min: gf("f_bp1_bp_min"),
      bp1_bp_max: gf("f_bp1_bp_max"),
      bp_ep_min: gf("f_bp_ep_min"),
      bp_ep_max: gf("f_bp_ep_max"),
      ep_oht_min: gf("f_ep_oht_min"),
      ep_oht_max: gf("f_ep_oht_max"),
      priority_prefix: g("f_priority_prefix"),
      xml_seq_index: gi("f_xml_seq"),
      xml_from: gi("f_xml_from"),
      xml_to: gi("f_xml_to"),
      xml_port_id: gi("f_xml_port"),
      usd_path: g("f_usd_path"),
      resource_index: parseInt($("f_resource_combo")?.value || "0", 10) || 0,
    };
  }

  // ---------------------------------------------------------------------------
  // apiUrl(path)
  // ---------------------------------------------------------------------------
  /** API_BASE 와 상대 경로를 이어 절대/베이스 URL 생성. */
  function apiUrl(path) {
    return API_BASE + path;
  }

  // ---------------------------------------------------------------------------
  // apiCommand(body)
  // ---------------------------------------------------------------------------
  /**
   * POST /api/command — Kit 브리지의 _dispatch_command 로 전달.
   * HTTP 스레드가 아닌 Kit 메인 스레드에서 실제 확장 함수가 실행된다.
   *
   * @param {object} body - 최소 { cmd: "..." }, cmd별 추가 필드는 README 참고.
   * @returns {Promise<object|null>} JSON 응답 (파싱 실패 시 null 가능)
   * @throws {Error} HTTP 오류 시 (본문 error 필드 또는 statusText)
   */
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
      /* 본문이 JSON이 아닐 수 있음 — 아래에서 텍스트로 에러 처리 */
    }
    if (!r.ok) {
      throw new Error((j && j.error) || t || r.statusText);
    }
    return j;
  }

  // ---------------------------------------------------------------------------
  // pollState()
  // ---------------------------------------------------------------------------
  /**
   * GET /api/state — 주기적으로 Kit 쪽 UI 스냅샷을 가져와 웹 DOM 에 반영.
   * Kit: kit_remote_http_bridge._snapshot(ext)
   *   - usd_status     ← load_window._load_status_label
   *   - progress       ← _sim_progress_label / _sim_progress_text
   *   - history        ← _sim_history_label / _sim_history_text
   *   - port_header, ports, ep3_visible ← 포트 상태 패널(_sim_port_cells 등)
   * 시뮬 본체(simulation_engine)는 직접 읽지 않고, 이미 Kit omni.ui 에 반영된 텍스트를 복사한다.
   */
  async function pollState() {
    try {
      const r = await fetch(apiUrl("/api/state"));
      if (!r.ok) throw new Error(r.statusText);
      const s = await r.json();

      // 연결 성공 배너 (kit_app 은 omni.kit.app 앱 이름)
      setBanner("Kit에 연결됨 — " + (s.kit_app || "OK"), true);

      // USD Load 창과 동일 의미의 상태 문자열
      $("usdStatus").textContent = s.usd_status || "";
      // 제어창 이력 라벨과 동일 소스에서 온 한 덩어리 텍스트
      $("simLine").textContent = s.sim_line || "";
      // 진행현황 / 이력로그 스크롤 영역 (표시모드에 따라 Kit에서도 숨김 처리되지만 웹은 별도 토글)
      $("logProgress").textContent = s.progress || "";
      $("logHistory").textContent = s.history || "";
      $("portHeader").textContent = s.port_header || "[포트상태]";

      // 포트 그리드: Kit 의 BP1/EP1… 라벨 텍스트와 동일한 occ 정보를 칸별로 표시
      const ports = s.ports || {};
      const names = ["BP2", "BP3", "BP4", "BP1", "EP1", "EP2", "EP3"];
      for (const p of names) {
        const cell = $("port_" + p);
        if (!cell) continue;
        const v = ports[p] != null ? String(ports[p]) : "-";
        cell.textContent = p + ":" + v;
        cell.className = "port-cell";
        const u = v.toUpperCase();
        if (u === "FULL") cell.classList.add("full");
        else if (v && v !== "-" && u !== "EMPTY") cell.classList.add("lot");
      }

      // EP3 칸: on_sim_ep_count_changed / EP 개수에 따른 가시성과 동기
      const ep3c = $("port_EP3");
      if (ep3c) {
        ep3c.classList.toggle("hidden", !s.ep3_visible);
      }

      // 웹 쪽 표시모드(진행만/이력만) 패널 토글
      applyLogModeVisibility(state.logMode);
    } catch (e) {
      setBanner(
        "Kit 브리지에 연결할 수 없습니다. Kit가 실행 중인지, TBS_REMOTE_UI=0 등으로 브리지가 꺼지지 않았는지 확인하세요. (" + e.message + ")",
        false
      );
    }
  }

  // ---------------------------------------------------------------------------
  // applyLogModeVisibility(mode)
  // ---------------------------------------------------------------------------
  /**
   * 표시모드: Kit 의 SimLogPanelMode(둘다/진행현황만/이력만)와 같은 0,1,2 의미.
   * Kit 에서 on_sim_log_view_changed 가 프레임 visible 을 바꾸고, 웹은 DOM 블록을 숨김 처리해
   * 사용자 경험을 맞춘다. (데이터는 pollState 가 계속 채우지만 보이는 영역만 제한.)
   */
  function applyLogModeVisibility(mode) {
    const prog = $("panelProgress")?.parentElement;
    const hist = $("panelHistory")?.parentElement;
    if (!prog || !hist) return;
    const m = parseInt(mode, 10) || 0;
    if (m === 0) {
      prog.classList.remove("hidden");
      hist.classList.remove("hidden");
    } else if (m === 1) {
      prog.classList.remove("hidden");
      hist.classList.add("hidden");
    } else {
      prog.classList.add("hidden");
      hist.classList.remove("hidden");
    }
  }

  // ---------------------------------------------------------------------------
  // loadResources()
  // ---------------------------------------------------------------------------
  /**
   * GET /api/resources — 샘플 USD 목록으로 f_resource_combo 옵션 채움.
   * Kit: usd_loader_utils.get_resource_usd_list() = load_window 의 resource 콤보와 동일.
   * "선택안함" 인덱스 0 은 get_load_path(ext) 에서 직접 경로 필드를 쓰는 것과 동일.
   */
  async function loadResources() {
    try {
      const r = await fetch(apiUrl("/api/resources"));
      if (!r.ok) return;
      const data = await r.json();
      const items = data.items || [];
      const sel = $("f_resource_combo");
      if (!sel) return;
      sel.innerHTML = "";
      const opt0 = document.createElement("option");
      opt0.value = "0";
      opt0.textContent = "선택안함";
      sel.appendChild(opt0);
      items.forEach((it, i) => {
        const o = document.createElement("option");
        o.value = String(i + 1);
        o.textContent = it.name || it.path || String(i);
        o.dataset.path = it.path || "";
        sel.appendChild(o);
      });
    } catch (_) {
      /* 목록 실패 시 콤보는 비어 있어도 경로 직접 입력으로 Load 가능 */
    }
  }

  // ---------------------------------------------------------------------------
  // onResourceChange()
  // ---------------------------------------------------------------------------
  /**
   * resource 콤보 변경 시 load_window.on_resource_combo_changed 와 동일:
   * 선택안함이 아니면 해당 샘플 경로를 경로 입력칸에 복사.
   */
  function onResourceChange() {
    const sel = $("f_resource_combo");
    if (!sel || sel.value === "0") return;
    const opt = sel.options[sel.selectedIndex];
    const p = opt?.dataset?.path;
    if (p) $("f_usd_path").value = p;
  }

  // ---------------------------------------------------------------------------
  // wireXmlSeqVisibility()
  // ---------------------------------------------------------------------------
  /**
   * XML 시퀀스 콤보 변경 시 FROM/TO 행 vs PORT_ID 행 표시 전환.
   * Kit: control_window.on_xml_seq_changed + xml_generator.FROM_TO_SEQS / PORT_ID_ONLY_SEQS
   *   FROM_TO 필요: MOVE_TRANSFERING, MOVE, MOVE_REQ → 콤보 인덱스 2,3,4
   *   그 외: READYTOLOAD, ARRIVED, READYTOUNLOAD, REMOVED → PORT_ID만
   */
  function wireXmlSeqVisibility() {
    const seq = parseInt($("f_xml_seq")?.value || "0", 10);
    const ab = $("xmlAbRow");
    const port = $("xmlPortRow");
    const useAb = seq >= 2 && seq <= 4;
    const usePort = !useAb;
    if (ab) ab.classList.toggle("hidden", !useAb);
    if (port) port.classList.toggle("hidden", !usePort);
  }

  // ---------------------------------------------------------------------------
  // syncEp3InitRow()
  // ---------------------------------------------------------------------------
  /**
   * EP 개수가 2일 때는 EP3 초기 적재 체크 행을 숨김.
   * Kit: on_sim_ep_count_changed(ext) 의 _sim_init_ep3_row.visible 와 동일한 의도.
   */
  function syncEp3InitRow() {
    const ep = $("f_ep_count");
    const row = $("row_init_ep3");
    if (!ep || !row) return;
    row.classList.toggle("hidden", ep.value !== "1");
  }

  // ---------------------------------------------------------------------------
  // init()
  // ---------------------------------------------------------------------------
  /**
   * 페이지 로드 시 한 번 실행:
   * - 이벤트 리스너 등록 (각 버튼 → apiCommand + Kit 함수 연결은 상단 표 참고)
   * - XML/EP3 UI 동기
   * - 샘플 USD 목록 로드
   * - 폴링 타이머 시작
   */
  async function init() {
    // EP 개수 변경 → EP3 초기적재 행 (control_window on_sim_ep_count_changed 와 대응)
    $("f_ep_count")?.addEventListener("change", syncEp3InitRow);
    syncEp3InitRow();

    // USD 샘플 콤보 (load_window)
    $("f_resource_combo")?.addEventListener("change", onResourceChange);

    // XML 제너레이터 입력 프레임 표시 (control_window on_xml_seq_changed)
    $("f_xml_seq")?.addEventListener("change", wireXmlSeqVisibility);

    // 표시모드 → Kit: log_mode cmd → on_sim_log_view_changed
    $("f_log_mode")?.addEventListener("change", async () => {
      state.logMode = parseInt($("f_log_mode").value, 10) || 0;
      applyLogModeVisibility(state.logMode);
      try {
        await apiCommand({ cmd: "log_mode", index: state.logMode });
      } catch (e) {
        console.warn(e);
      }
    });

    // Load → load_window.on_load_usd(ext) (비동기 open_stage)
    $("btnLoadUsd")?.addEventListener("click", async () => {
      try {
        await apiCommand({
          cmd: "load_usd",
          path: $("f_usd_path").value.trim(),
          resource_index: parseInt($("f_resource_combo")?.value || "0", 10) || 0,
        });
      } catch (e) {
        alert(e.message);
      }
    });

    // 시뮬 시작 → _apply_web_fields + on_sim_start_clicked → TBSSimulationEngine
    $("btnSimStart")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "sim_start", fields: collectFields() });
      } catch (e) {
        alert(e.message);
      }
    });
    // 정지 → on_sim_stop_clicked (스레드·러너 정리 등)
    $("btnSimStop")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "sim_stop" });
      } catch (e) {
        alert(e.message);
      }
    });
    // 리셋 → on_sim_reset_clicked
    $("btnSimReset")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "sim_reset" });
      } catch (e) {
        alert(e.message);
      }
    });
    // prim 목록 → refresh_object_list(ext) (제어창 하단 장비 prim 리스트)
    $("btnPrimRefresh")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "prim_refresh" });
      } catch (e) {
        alert(e.message);
      }
    });
    // 진행현황 복사 → on_copy_sim_progress(ext) (omni.kit.clipboard)
    $("btnCopyProgress")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "copy_progress" });
      } catch (e) {
        alert(e.message);
      }
    });
    // XML OK → _apply_web_fields + on_xml_ok_clicked (ext._last_generated_xml 설정)
    $("btnXmlOk")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "xml_ok", fields: collectFields() });
      } catch (e) {
        alert(e.message);
      }
    });
    // 제너레이터 역파싱 → on_xml_run_clicked
    $("btnXmlRun")?.addEventListener("click", async () => {
      try {
        await apiCommand({ cmd: "xml_run" });
      } catch (e) {
        alert(e.message);
      }
    });

    wireXmlSeqVisibility();
    await loadResources();
    setInterval(pollState, POLL_MS);
    pollState();
  }

  // ---------------------------------------------------------------------------
  // 엔트리: DOM 준비 후 init
  // ---------------------------------------------------------------------------
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

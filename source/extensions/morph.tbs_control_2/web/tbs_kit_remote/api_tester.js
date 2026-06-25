/**
 * TBS Kit API 테스터 — GET /api/registry 기반 cmd·state 실시간 검증
 *
 * 사용:
 *   1) Kit 실행 (morph.tbs_control_2, TBS_REMOTE_UI 켜짐)
 *   2) http://127.0.0.1:8720/api_tester.html
 */

(function () {
  const DEFAULT_API =
    (typeof window !== "undefined" && window.TBS_KIT_REMOTE_API) || "";
  const POLL_STATE_MS = 1000;

  let registry = null;
  let pollTimer = null;
  let lastState = null;
  const logLines = [];

  function $(id) {
    return document.getElementById(id);
  }

  function apiBase() {
    const raw = ($("apiBase") && $("apiBase").value.trim()) || DEFAULT_API;
    return raw.replace(/\/$/, "");
  }

  function apiUrl(path) {
    const base = apiBase();
    const p = path.startsWith("/") ? path : "/" + path;
    return base ? base + p : p;
  }

  function setBanner(msg, ok) {
    const el = $("connBanner");
    if (!el) return;
    el.textContent = msg;
    el.className = "banner " + (ok ? "ok" : "warn");
  }

  function appendLog(label, data) {
    const ts = new Date().toLocaleTimeString();
    let body;
    try {
      body = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    } catch (e) {
      body = String(data);
    }
    logLines.unshift("[" + ts + "] " + label + "\n" + body);
    if (logLines.length > 40) logLines.length = 40;
    const pre = $("responseLog");
    if (pre) pre.textContent = logLines.join("\n\n---\n\n");
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    const text = await res.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (e) {
      json = { _raw: text };
    }
    if (!res.ok) {
      const err = new Error("HTTP " + res.status);
      err.response = json;
      err.status = res.status;
      throw err;
    }
    return json;
  }

  async function loadRegistry() {
    const url = apiUrl("/api/registry");
    registry = await fetchJson(url);
    appendLog("GET /api/registry", registry);
    renderCommands();
    renderStateFields();
    renderFieldsTable();
    setBanner(
      "연결됨 — cmd " +
        (registry.commands_exposed || []).length +
        "개 노출, 계획 " +
        (registry.commands_planned || []).length +
        "개",
      true
    );
    return registry;
  }

  async function postCommand(body) {
    const url = apiUrl("/api/command");
    const res = await fetchJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    appendLog("POST /api/command " + (body.cmd || "?"), res);
    return res;
  }

  async function fetchState() {
    const url = apiUrl("/api/state");
    lastState = await fetchJson(url);
    const pre = $("stateJson");
    if (pre) pre.textContent = JSON.stringify(lastState, null, 2);
    highlightSelectedStateField();
    return lastState;
  }

  function renderCommands() {
    const host = $("commandsList");
    if (!host || !registry) return;
    host.innerHTML = "";
    const exposedOnly = $("chkExposedOnly") && $("chkExposedOnly").checked;
    const list = exposedOnly ? registry.commands_exposed || [] : registry.commands || [];

    list.forEach(function (meta) {
      const card = document.createElement("div");
      card.className = "api-cmd-card" + (meta.exposed === false ? " planned" : "");

      const title = document.createElement("h3");
      title.innerHTML = "<code>" + escapeHtml(meta.cmd) + "</code>";
      card.appendChild(title);

      const desc = document.createElement("p");
      desc.className = "api-cmd-meta";
      desc.textContent = meta.summary || "";
      card.appendChild(desc);

      const metaRow = document.createElement("div");
      metaRow.className = "api-cmd-meta";
      metaRow.innerHTML =
        "<span>handler: <code>" +
        escapeHtml(meta.handler || "") +
        "</code></span>" +
        (meta.web_ui ? "<span>UI: " + escapeHtml(meta.web_ui) + "</span>" : "");
      card.appendChild(metaRow);

      if (meta.notes) {
        const notes = document.createElement("p");
        notes.className = "api-cmd-meta";
        notes.textContent = meta.notes;
        card.appendChild(notes);
      }

      const badge = document.createElement("span");
      badge.className = "api-cmd-badge" + (meta.exposed === false ? " planned" : "");
      badge.textContent = meta.exposed === false ? "계획 (미연결)" : "노출됨";
      card.appendChild(badge);

      const ta = document.createElement("textarea");
      ta.value = JSON.stringify(meta.example_body || { cmd: meta.cmd }, null, 2);
      card.appendChild(ta);

      const actions = document.createElement("div");
      actions.className = "api-cmd-actions";

      const btnRun = document.createElement("button");
      btnRun.type = "button";
      btnRun.textContent = "실행 (POST)";
      btnRun.disabled = meta.exposed === false;
      btnRun.addEventListener("click", function () {
        let body;
        try {
          body = JSON.parse(ta.value);
        } catch (e) {
          appendLog("JSON 파싱 오류", e.message);
          alert("JSON 형식이 올바르지 않습니다.");
          return;
        }
        btnRun.disabled = true;
        postCommand(body)
          .then(function () {
            return fetchState().catch(function () {});
          })
          .catch(function (err) {
            appendLog("POST 실패", err.response || err.message);
          })
          .finally(function () {
            btnRun.disabled = meta.exposed === false;
          });
      });
      actions.appendChild(btnRun);

      const btnFmt = document.createElement("button");
      btnFmt.type = "button";
      btnFmt.textContent = "예시로 되돌리기";
      btnFmt.addEventListener("click", function () {
        ta.value = JSON.stringify(meta.example_body || { cmd: meta.cmd }, null, 2);
      });
      actions.appendChild(btnFmt);

      card.appendChild(actions);
      host.appendChild(card);
    });
  }

  let selectedStateKey = null;

  function renderStateFields() {
    const ul = $("stateFieldList");
    if (!ul || !registry) return;
    ul.innerHTML = "";
    (registry.state_fields || []).forEach(function (f) {
      const li = document.createElement("li");
      li.dataset.key = f.key;
      li.innerHTML =
        '<span class="key">' +
        escapeHtml(f.key) +
        "</span><br/>" +
        escapeHtml(f.summary || "");
      li.addEventListener("click", function () {
        selectedStateKey = f.key;
        Array.prototype.forEach.call(ul.querySelectorAll("li"), function (el) {
          el.classList.toggle("selected", el.dataset.key === selectedStateKey);
        });
        highlightSelectedStateField();
      });
      ul.appendChild(li);
    });
  }

  function highlightSelectedStateField() {
    if (!selectedStateKey || !lastState) return;
    const val = getByPath(lastState, selectedStateKey);
    const pre = $("stateJson");
    if (!pre) return;
    const snippet = {};
    snippet[selectedStateKey] = val;
    pre.textContent =
      "// 선택: " +
      selectedStateKey +
      "\n" +
      JSON.stringify(snippet, null, 2) +
      "\n\n// 전체 state\n" +
      JSON.stringify(lastState, null, 2);
  }

  function getByPath(obj, key) {
    if (!obj || !key) return undefined;
    if (Object.prototype.hasOwnProperty.call(obj, key)) return obj[key];
    return undefined;
  }

  function renderFieldsTable() {
    const tbody = $("fieldsTable") && $("fieldsTable").querySelector("tbody");
    if (!tbody || !registry) return;
    tbody.innerHTML = "";
    (registry.web_fields || []).forEach(function (f) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td><code>" +
        escapeHtml(f.key) +
        "</code></td>" +
        "<td>" +
        escapeHtml(f.summary || "") +
        "</td>" +
        "<td><code>" +
        escapeHtml(f.kit_model || "") +
        "</code></td>" +
        "<td>" +
        escapeHtml(f.type_hint || "") +
        "</td>" +
        "<td><code>" +
        escapeHtml(JSON.stringify(f.example)) +
        "</code></td>";
      tbody.appendChild(tr);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function switchTab(name) {
    const panels = {
      commands: $("panelCommands"),
      state: $("panelState"),
      fields: $("panelFields"),
      log: $("panelLog"),
    };
    Object.keys(panels).forEach(function (k) {
      if (panels[k]) panels[k].classList.toggle("hidden", k !== name);
    });
    document.querySelectorAll(".api-tester-tabs .tab").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.tab === name);
    });
    if (name === "state" && $("chkPollState") && $("chkPollState").checked) {
      startStatePoll();
    }
  }

  function startStatePoll() {
    stopStatePoll();
    fetchState().catch(function (err) {
      appendLog("GET /api/state 실패", err.response || err.message);
    });
    pollTimer = setInterval(function () {
      if (!$("chkPollState") || !$("chkPollState").checked) return;
      fetchState().catch(function () {});
    }, POLL_STATE_MS);
  }

  function stopStatePoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function initTabs() {
    document.querySelectorAll(".api-tester-tabs .tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        switchTab(btn.dataset.tab);
      });
    });
  }

  function init() {
    const baseInput = $("apiBase");
    if (baseInput && DEFAULT_API) baseInput.placeholder = DEFAULT_API;

    initTabs();

    $("btnReloadRegistry") &&
      $("btnReloadRegistry").addEventListener("click", function () {
        loadRegistry().catch(function (err) {
          setBanner("연결 실패 — Kit 실행 및 포트 8720 확인", false);
          appendLog("registry 로드 실패", err.response || err.message);
        });
      });

    $("chkExposedOnly") &&
      $("chkExposedOnly").addEventListener("change", renderCommands);

    $("btnFetchState") &&
      $("btnFetchState").addEventListener("click", function () {
        fetchState().catch(function (err) {
          appendLog("state 실패", err.response || err.message);
        });
      });

    $("chkPollState") &&
      $("chkPollState").addEventListener("change", function () {
        if ($("chkPollState").checked) startStatePoll();
        else stopStatePoll();
      });

    $("btnClearLog") &&
      $("btnClearLog").addEventListener("click", function () {
        logLines.length = 0;
        const pre = $("responseLog");
        if (pre) pre.textContent = "(응답 없음)";
      });

    loadRegistry()
      .then(function () {
        startStatePoll();
      })
      .catch(function (err) {
        setBanner(
          "연결 실패 — Kit를 실행한 뒤 http://127.0.0.1:8720/api_tester.html 을 여세요",
          false
        );
        appendLog("초기 registry 실패", err.response || err.message);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

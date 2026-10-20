function createCasework(api) {
  const el = (id) => document.getElementById(id);
  const labels = {
    checking: "Needs checking",
    waiting: "Waiting for hotel",
    resolved: "Resolved",
  };
  const findings = {
    amount_mismatch: "Amount difference",
    currency_mismatch: "Currency difference",
    ambiguous: "Duplicate / ambiguous",
    statement_only: "Statement only",
    ledger_only: "Ledger only",
  };
  let filterValue = "all";
  let view = "statement",
    board = null,
    selected = "",
    dirty = false,
    saving = false,
    generation = 0,
    pending = null,
    quality = null,
    loading = 0;
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const when = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? "Time unavailable"
      : new Intl.DateTimeFormat("en-GB", {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(date);
  };
  function message(id, text, error = false) {
    el(id).textContent = text;
    el(id).dataset.tone = error ? "error" : "info";
  }
  function current() {
    return api.getState();
  }
  function valid(state, version) {
    const now = current();
    return (
      now.connected &&
      now.reference === state.reference &&
      now.epoch === state.epoch &&
      version === generation
    );
  }
  function allowLeave() {
    if (saving) {
      message(
        "case-feedback",
        "Wait for the follow-up save to finish before leaving.",
      );
      return false;
    }
    if (api.allowExtraLeave && !api.allowExtraLeave()) return false;
    if (!dirty) return true;
    if (
      !window.confirm(
        "Discard your unsaved follow-up note? Saved history will stay unchanged.",
      )
    )
      return false;
    dirty = false;
    pending = null;
    el("case-note").value = "";
    return true;
  }
  function switchView(name, force = false) {
    if (name !== view && !force && !allowLeave()) return false;
    if (!current().connected) return false;
    const changed = name !== view;
    view = name;
    const titles = {
      statement: "Hotel statement review",
      followup: "Invoice follow-up",
      quality: "Quality & evidence",
      responses: "Hotel replies",
      comparison: "Compare statements",
      about: "How this works",
      decisions: "🧪 AI model comparison",
    };
    el("workspace-title").textContent = titles[name] || "Supplier workspace";
    for (const key of [
      "statement",
      "followup",
      "quality",
      "responses",
      "comparison",
      "about",
      "decisions",
    ]) {
      el(`${key}-view`).hidden = key !== name;
      el(`view-${key}`).setAttribute("aria-pressed", String(key === name));
    }
    if (changed) {
      el("workspace-title").scrollIntoView({
        block: "start",
        behavior: "auto",
      });
      el("workspace-title").focus({ preventScroll: true });
    }
    return true;
  }
  function stateFor(id) {
    return board?.cases[id]?.status || "checking";
  }
  function balances(row) {
    const result = [];
    for (const [title, lines, field] of [
      ["Statement", row.statement_lines, "claimed_amount"],
      ["Open ledger", row.ledger_lines, "outstanding_amount"],
    ]) {
      const card = node("div", null, "balance-card");
      card.append(node("span", title));
      card.append(
        node(
          "strong",
          lines.length
            ? lines
                .slice(0, 3)
                .map((line) => `${line.currency} ${line[field]}`)
                .join(" · ")
            : "Not recorded",
        ),
      );
      if (lines.length > 3)
        card.append(
          node(
            "small",
            `First 3 of ${lines.length} rows. Open source evidence for all rows.`,
          ),
        );
      result.push(card);
    }
    return result;
  }
  function syncControls() {
    const locked = saving || !current().canEdit;
    for (const id of ["case-actor", "case-note", "case-status"])
      el(id).disabled = locked;
    for (const button of el("case-list").querySelectorAll(".case-card"))
      button.disabled = saving;
    el("save-case").disabled = locked || !board?.can_save;
  }
  function detail() {
    const row = board?.rows.find((item) => item.id === selected);
    el("case-detail").hidden = !row;
    if (!row) return;
    el("case-title").textContent = row.invoice_ref;
    el("case-finding").textContent =
      `${findings[row.status] || row.status.replaceAll("_", " ")} · ${row.id}`;
    el("case-balances").replaceChildren(...balances(row));
    if (!dirty) el("case-status").value = stateFor(selected);
    const history = board.history
      .filter((item) => item.evidence_id === selected)
      .slice()
      .reverse();
    el("case-history").replaceChildren();
    for (const entry of history) {
      const item = node("li");
      item.append(
        node(
          "strong",
          `${labels[entry.from_status]} → ${labels[entry.status]}`,
        ),
        node("p", entry.note),
        node("small", `${entry.actor} · ${when(entry.created_at)}`),
      );
      el("case-history").append(item);
    }
    if (api.describeOutcome) {
      const result = api.describeOutcome(row, history[0]);
      el("case-outcome-status").textContent = labels[result.status];
      el("case-outcome").replaceChildren();
      for (const [title, text] of [
        [
          "Original finding",
          findings[row.status] || row.status.replaceAll("_", " "),
        ],
        ["Assistant explanation", result.assistant],
        ["Human decision", result.decision],
        ["What remains", result.remaining],
      ]) {
        const section = node("section");
        section.append(node("h5", title), node("p", text));
        el("case-outcome").append(section);
      }
    }
    if (!history.length)
      el("case-history").append(
        node("li", "No follow-up recorded. Source findings are unchanged."),
      );
    syncControls();
    if (!board.can_save)
      message(
        "case-feedback",
        "The 500-action limit is reached. Your existing history is preserved.",
        true,
      );
  }
  function renderBoard() {
    const counts = { checking: 0, waiting: 0, resolved: 0 };
    for (const row of board.rows) counts[stateFor(row.id)]++;
    el("followup-count").textContent = String(
      board.rows.length - counts.resolved,
    );
    el("followup-summary").textContent =
      `${counts.checking} need checking · ${counts.waiting} waiting for hotel · ${counts.resolved} resolved. ${board.rows.length} original source differences.`;
    const filter = el("case-filter").value,
      rows = board.rows.filter(
        (row) => filter === "all" || stateFor(row.id) === filter,
      );
    filterValue = filter;
    if (!rows.some((row) => row.id === selected)) selected = rows[0]?.id || "";
    el("case-list").replaceChildren();
    for (const row of rows) {
      const button = node("button", null, "case-card");
      button.type = "button";
      button.setAttribute("aria-pressed", String(row.id === selected));
      button.append(
        node("strong", row.invoice_ref),
        node("span", findings[row.status] || row.status.replaceAll("_", " ")),
        node(
          "span",
          labels[stateFor(row.id)],
          `case-status status-${stateFor(row.id)}`,
        ),
      );
      button.addEventListener("click", () => {
        if (row.id !== selected && !allowLeave()) return;
        selected = row.id;
        message("case-feedback", "");
        renderBoard();
      });
      el("case-list").append(button);
    }
    if (!rows.length)
      el("case-list").append(
        node(
          "p",
          board.rows.length
            ? "No cases match this status."
            : "All invoice groups match. No follow-up cases are needed.",
          "helper",
        ),
      );
    if (!selected && rows.length) selected = rows[0].id;
    detail();
  }
  function followupOpened() {
    const row = board?.rows.find((item) => item.id === selected);
    if (row)
      document.dispatchEvent(
        new CustomEvent("workspace-followup-opened", {
          detail: {
            reference: current().reference,
            invoiceRef: row.invoice_ref,
          },
        }),
      );
  }
  async function showFollowup({ refresh = false, invoiceRef = "" } = {}) {
    if (!switchView("followup")) return;
    if (invoiceRef) {
      if (!allowLeave()) return;
      el("case-filter").value = "all";
    }
    function selectRequested() {
      if (invoiceRef)
        selected =
          board.rows.find((row) => row.invoice_ref === invoiceRef)?.id || "";
    }
    if (board && !refresh) {
      selectRequested();
      renderBoard();
      followupOpened();
      return;
    }
    const state = current(),
      version = generation,
      load = ++loading;
    message("case-feedback", "");
    el("followup-summary").textContent = "Loading saved follow-up…";
    try {
      const value = await api.read(`/runs/${state.reference}/cases`);
      if (!valid(state, version) || load !== loading) return;
      board = value;
      selectRequested();
      renderBoard();
      followupOpened();
    } catch (error) {
      if (valid(state, version) && load === loading) {
        api.expired(error);
        el("followup-summary").textContent =
          `${error.message}. Follow-up could not load. Your source evidence is unchanged. Use Refresh follow-up to try again.`;
        message(
          "case-feedback",
          `${error.message}. Use Refresh follow-up to try again.`,
          true,
        );
      }
    }
  }
  const checkLabels = {
    saved_integrity: "Saved report integrity",
    source_files_unchanged: "Original source files preserved",
    current_reconciliation_matches:
      "Recomputed amounts agree with saved result",
    no_transmission: "No supplier message transmitted",
    only_allowed_tools: "Recorded tool boundary",
    model_budget: "Model request budget",
    last_tool_budget: "Final tool request budget",
    required_workflow_tools: "Required workflow tools recorded",
    factual_query: "Draft matches the financial evidence",
  };
  function renderTimeline(data) {
    const list = el("activity-list");
    list.replaceChildren();
    for (const entry of data.timeline) {
      const li = node("li", null, `timeline-event timeline-${entry.state}`),
        details = node("details"),
        summary = node("summary", entry.label);
      details.append(summary, node("p", entry.detail));
      for (const id of entry.evidence_ids) {
        const row = current().saved?.result.rows.find((item) => item.id === id);
        if (!row) continue;
        const button = node(
          "button",
          `View evidence · ${row.invoice_ref}`,
          "quiet-link",
        );
        button.type = "button";
        button.addEventListener("click", () => {
          if (switchView("statement"))
            api
              .openEvidence(row.invoice_ref)
              .catch((error) => api.feedback(error.message));
        });
        details.append(button);
      }
      li.append(details);
      list.append(li);
    }
    if (!data.timeline.length)
      list.append(
        node("li", "No agent actions are recorded for this statement."),
      );
  }
  function renderQuality(data) {
    quality = data;
    el("quality-context").textContent =
      `${data.current.source_kind === "uploaded" ? "Uploaded statement / sample finance ledger" : "Sample finance records"} · ${{ offline_template: "Template ready; no completed agent investigation", live_model_complete: "Live investigation complete", offline_model_verified: "Local model verification", provider_error: "Investigation could not finish", timeout: "Investigation timed out" }[data.current.execution_kind] || "Investigation incomplete"}.`;
    const values = [
      ["Investigation model", data.current.model || "Not used"],
      ["Investigation requests", data.current.model_calls],
      ["Investigation tokens", data.current.reported_tokens ?? "Not reported"],
      [
        "Investigation duration",
        !data.current.model_calls
          ? "Not run"
          : data.current.duration_seconds == null
            ? "Not reported"
            : `${data.current.duration_seconds}s`,
      ],
    ];
    el("quality-usage").replaceChildren(
      ...values.map(([label, value]) => {
        const item = node("div");
        item.append(node("span", label), node("strong", String(value)));
        return item;
      }),
    );
    el("quality-checks").replaceChildren(
      ...Object.entries(data.current.checks).map(([key, state]) => {
        const item = node("li");
        item.append(
          node("span", checkLabels[key] || key),
          node(
            "strong",
            state === "passed"
              ? "Passed"
              : state === "not_run"
                ? "Not run"
                : state === "unavailable"
                  ? "Unavailable"
                  : "Failed",
            `check-state check-${state}`,
          ),
        );
        return item;
      }),
    );
    const history = data.historical;
    el("historical-metrics").replaceChildren();
    el("evaluation-cases").replaceChildren();
    el("evaluation-provenance").textContent = "";
    if (!history.available) {
      el("historical-context").textContent = history.message;
      return;
    }
    el("historical-context").textContent =
      `${history.scope} · reviewed ${history.date}. Original scores and thresholds retained.`;
    for (const [key, label] of [
      ["tool_trajectory_avg_score", "Tool trajectory"],
      ["response_match_score", "Wording similarity"],
    ]) {
      const metric = history.metrics[key],
        card = node("div", null, "metric-card");
      card.append(
        node("span", label),
        node("strong", `${metric.passed} / ${metric.total} passed`),
        node(
          "p",
          `${metric.total - metric.passed} failed · threshold ${history.thresholds[key]}`,
        ),
      );
      el("historical-metrics").append(card);
    }
    for (const entry of history.cases) {
      const details = node("details", null, "evaluation-case"),
        summary = node("summary"),
        word = entry.metrics.response_match_score;
      summary.append(
        node("strong", entry.id.replaceAll("_", " ")),
        node(
          "span",
          `Wording ${word.status.toLowerCase()} · ${word.score.toFixed(3)}`,
          word.status === "PASSED" ? "check-passed" : "check-failed",
        ),
      );
      details.append(summary);
      for (const [title, text] of [
        ["Prompt", entry.prompt],
        ["Expected reference", entry.expected],
        ["Saved response", entry.actual],
      ])
        details.append(node("h4", title), node("p", text));
      details.append(
        node(
          "p",
          `Tool trajectory: ${entry.metrics.tool_trajectory_avg_score.status.toLowerCase()} · wording threshold ${word.threshold}. These are saved sample evaluations, not checks of your uploaded CSV.`,
          "helper",
        ),
      );
      el("evaluation-cases").append(details);
    }
    el("evaluation-provenance").textContent =
      `Configuration SHA-256: ${history.config_sha256}. Sources: ${history.sources.map((item) => `${item.file} (${item.sha256})`).join("; ")}`;
    renderTimeline(data);
  }
  async function loadQuality({ show = true } = {}) {
    if (show && !switchView("quality")) return;
    const state = current(),
      version = generation;
    message("quality-feedback", "Checking the saved evidence…");
    try {
      const value = await api.read(`/runs/${state.reference}/quality`);
      if (!valid(state, version)) return;
      renderQuality(value);
      message(
        "quality-feedback",
        "Checks loaded from saved evidence. No model request was made.",
      );
    } catch (error) {
      if (valid(state, version)) {
        api.expired(error);
        message(
          "quality-feedback",
          `${error.message}. Checks are unavailable; no pass is claimed.`,
          true,
        );
      }
    }
  }
  async function save(event) {
    event.preventDefault();
    if (saving || !board || !selected) return;
    const state = current(),
      version = generation,
      invoiceRef = board.rows.find((item) => item.id === selected)?.invoice_ref;
    let recorded = false;
    const body = {
      request_id: pending?.request_id || crypto.randomUUID(),
      expected_generation: board.generation,
      status: el("case-status").value,
      actor: el("case-actor").value.trim(),
      note: el("case-note")
        .value.replace(/[\r\n\t]+/g, " ")
        .trim(),
    };
    pending = body;
    saving = true;
    detail();
    message("case-feedback", "Saving follow-up…");
    try {
      const value = await api.write(
        `/runs/${state.reference}/cases/${encodeURIComponent(selected)}/actions`,
        body,
      );
      if (!valid(state, version)) return;
      board = value;
      dirty = false;
      pending = null;
      el("case-note").value = "";
      renderBoard();
      message(
        "case-feedback",
        "Update saved. Financial records are unchanged.",
      );
      recorded = true;
    } catch (error) {
      if (valid(state, version)) {
        api.expired(error);
        message(
          "case-feedback",
          `${error.message}. Your note is retained. Refresh follow-up to inspect saved history before retrying.`,
          true,
        );
      }
    } finally {
      if (valid(state, version)) {
        saving = false;
        detail();
        if (recorded)
          document.dispatchEvent(
            new CustomEvent("workspace-case-saved", {
              detail: {
                reference: state.reference,
                invoiceRef,
                status: body.status,
              },
            }),
          );
      }
    }
  }
  el("case-form").addEventListener("submit", save);
  for (const id of ["case-status", "case-actor", "case-note"])
    el(id).addEventListener("input", () => {
      dirty = true;
      pending = null;
    });
  el("case-filter").addEventListener("change", () => {
    if (!allowLeave()) {
      el("case-filter").value = filterValue;
      return;
    }
    if (board) renderBoard();
  });
  el("case-evidence").addEventListener("click", () => {
    const row = board?.rows.find((item) => item.id === selected);
    if (row && switchView("statement"))
      api
        .openEvidence(row.invoice_ref)
        .catch((error) => api.feedback(error.message));
  });
  el("refresh-cases").addEventListener("click", () =>
    showFollowup({ refresh: true }),
  );
  el("refresh-quality").addEventListener("click", () => loadQuality());
  el("statements-link").addEventListener("click", (event) => {
    if (!switchView("statement")) event.preventDefault();
  });
  el("view-statement").addEventListener("click", () => switchView("statement"));
  el("view-followup").addEventListener("click", () =>
    showFollowup({ invoiceRef: api.preferredInvoice?.() || "" }),
  );
  el("view-quality").addEventListener("click", () => loadQuality());
  return {
    showView: (name) => switchView(name),
    invalidateCases() {
      board = null;
    },
    showFollowup,
    showQuality: loadQuality,
    showStatement: () => switchView("statement"),
    allowLeave,
    syncControls,
    async showTimeline() {
      if (!switchView("statement")) return;
      api.selectActivity();
      await loadQuality({ show: false });
    },
    reset() {
      generation++;
      loading++;
      board = null;
      quality = null;
      selected = "";
      dirty = false;
      saving = false;
      pending = null;
      view = "statement";
      el("workspace-title").textContent = "Hotel statement review";
      for (const id of [
        "case-list",
        "case-history",
        "case-outcome",
        "quality-checks",
        "quality-usage",
        "evaluation-cases",
        "historical-metrics",
      ])
        el(id).replaceChildren();
      for (const id of ["case-note", "case-actor"]) el(id).value = "";
      for (const id of [
        "case-feedback",
        "case-outcome-status",
        "quality-feedback",
        "historical-context",
        "quality-context",
        "evaluation-provenance",
      ])
        el(id).textContent = "";
      el("case-detail").hidden = true;
      el("followup-summary").textContent = "Open this view to load follow-up.";
      el("followup-count").textContent = "";
      for (const key of [
        "statement",
        "followup",
        "quality",
        "responses",
        "comparison",
        "about",
        "decisions",
      ]) {
        el(`${key}-view`).hidden = key !== "statement";
        el(`view-${key}`).setAttribute(
          "aria-pressed",
          String(key === "statement"),
        );
      }
    },
    isReady: () => Boolean(board),
    qualityReady: () => Boolean(quality),
  };
}

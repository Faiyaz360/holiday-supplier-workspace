"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const embeddedCss = null;
  const onboardingFactory = null;
  const caseworkFactory = null;
  const repliesFactory = null;
  const comparisonFactory = null;
  const experienceFactory = null;
  const decisionsFactory = null;
  const identityMode = false;
  const passwordMode = false;
  let expiryTimer = null,
    newReviewRequestId = "";
  let importRaw = null,
    importPreview = null,
    importRequestId = "",
    importVersion = 0;
  let questionRequest = null,
    conversationPending = false,
    conversationLimit = false,
    conversationPoll = null,
    evidenceToken = "";
  let questionRecords = [],
    pendingScenario = "";
  let recoveryBusy = false,
    recoveryVersion = 0,
    recoveryState = "";
  let evidenceReady = false,
    openedEvidence = "",
    focusedEvidence = "";
  const guideEvent = (type, detail) =>
    document.dispatchEvent(new CustomEvent(type, { detail }));
  let pass = "",
    capabilities = null,
    saved = null,
    runId = "",
    busy = false,
    epoch = 0;
  let pollTimer = null,
    localMode = false,
    currentProfile = null,
    investigating = false;
  const requests = new Set();
  const complete = () =>
    saved &&
    ["live_model_complete", "offline_model_verified"].includes(
      saved.meta.drafting_status,
    );
  const running = () => saved?.execution.status === "running";
  const displayDate = (value) =>
    /^\d{4}-\d{2}-\d{2}$/.test(value || "")
      ? new Intl.DateTimeFormat("en-GB", {
          day: "numeric",
          month: "short",
          year: "numeric",
          timeZone: "UTC",
        }).format(new Date(`${value}T00:00:00Z`))
      : value || "—";
  function status(title, detail, tone = "info") {
    el("status-title").textContent = title;
    el("status-detail").textContent = detail;
    el("workspace-status").dataset.tone = tone;
  }
  function selectTab(name, focus = false) {
    for (const tab of ["assistant", "query", "activity"]) {
      const selected = tab === name;
      el(`tab-${tab}`).setAttribute("aria-selected", String(selected));
      el(`tab-${tab}`).tabIndex = selected ? 0 : -1;
      el(`panel-${tab}`).hidden = !selected;
    }
    if (focus) el(`tab-${name}`).focus();
  }
  function step(id, done, message) {
    el(id).dataset.done = String(done);
    el(id).querySelector(".step-result").textContent = message;
  }
  function controls() {
    casework.syncControls();
    replies.syncControls();
    comparison.syncControls();
    experience.syncControls();
    decisions.syncControls();
    const connected = Boolean(capabilities),
      frozen = Boolean(saved?.meta.review);
    for (const id of [
      "statement-file",
      "use-sample",
      "download-sample",
      "sample-scenario",
    ])
      el(id).disabled = busy || !capabilities?.permissions.create;
    el("confirm-import").disabled =
      busy || !importPreview || !capabilities?.permissions.create;
    const questionDisabled =
      busy ||
      !saved ||
      conversationPending ||
      conversationLimit ||
      !capabilities?.permissions.execute ||
      !capabilities?.questions_enabled;
    for (const id of [
      "question-input",
      "ask-question",
      "suggest-invoice",
      "suggest-priority",
    ])
      el(id).disabled = questionDisabled;
    el("connect").disabled = busy;
    el("access-pass").disabled = busy;
    el("disconnect").disabled = !connected && !busy;
    el("create-run").disabled = busy || !capabilities?.permissions.create;
    el("create-run").hidden = Boolean(saved);
    el("open-run").disabled = busy || !connected;
    el("new-review").hidden =
      !saved ||
      !(frozen || complete() || conversationLimit) ||
      !capabilities?.permissions.create;
    el("new-review").disabled = busy || !capabilities?.permissions.create;
    el("reload-run").disabled = busy || !connected || !runId;
    el("copy-query").disabled = busy || !saved;
    el("export-query").disabled = busy || !saved;
    el("show-query").disabled = !saved;
    el("run-reference").disabled = busy;
    el("execute-run").disabled =
      busy ||
      !saved ||
      running() ||
      frozen ||
      complete() ||
      !capabilities?.permissions.execute ||
      !capabilities?.execution_enabled ||
      saved.meta.model_calls >= 10;
    el("review-run").disabled =
      busy ||
      !saved ||
      running() ||
      frozen ||
      !capabilities?.permissions.review;
    el("reviewer-name").disabled = el("review-run").disabled;
    el("review-confirm").disabled = el("review-run").disabled;
    el("review-form").hidden = !capabilities?.permissions.review || frozen;
    el("switch-reviewer").hidden =
      !localMode ||
      !saved ||
      frozen ||
      Boolean(capabilities?.permissions.review) ||
      busy;
    for (const id of ["role-analyst", "role-reviewer", "switch-reviewer"])
      el(id).disabled = busy;
    if (!connected || !saved)
      el("execution-note").textContent = "Open a statement to begin.";
    else if (frozen)
      el("execution-note").textContent =
        "Review recorded. This statement is now read-only.";
    else if (complete())
      el("execution-note").textContent =
        "The query is saved. Open Supplier email to check and review it.";
    else if (running() || investigating)
      el("execution-note").textContent =
        "Working on this statement. Another investigation cannot start while it runs.";
    else if (!capabilities.permissions.execute)
      el("execution-note").textContent =
        "Your reviewer role can check the query. An accounts analyst starts investigations.";
    else if (!capabilities.execution_enabled)
      el("execution-note").textContent =
        "Model access is currently unavailable. The invoice evidence and template query are still available.";
    else if (saved.meta.model_calls >= 10)
      el("execution-note").textContent =
        "This run has reached its request limit. Check the saved outcome before taking further action.";
    else
      el("execution-note").textContent =
        "One investigation, up to two minutes. The assistant prepares a query; you decide what to do next.";
    el("execute-label").textContent =
      running() || investigating
        ? "Investigation in progress"
        : complete()
          ? "Investigation complete"
          : "Investigate differences";
    el("show-query").firstChild.textContent = complete()
      ? "View supplier email "
      : "Preview email draft ";
  }
  async function request(path, body, timeout = 20000, options = {}) {
    const controller = new AbortController();
    requests.add(controller);
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        credentials: "same-origin",
        headers: {
          ...(pass ? { Authorization: `Bearer ${pass}` } : {}),
          ...(body === undefined
            ? {}
            : {
                "Content-Type": "application/json",
                "X-Workspace-Request": "1",
              }),
          ...options.headers,
        },
        body:
          body === undefined
            ? undefined
            : options.raw
              ? body
              : JSON.stringify(body),
        cache: "no-store",
        redirect: "error",
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail;
        try {
          detail = (await response.json()).detail;
        } catch {
          /* Use the safe fallback. */
        }
        const error = new Error(
          typeof detail === "string"
            ? detail
            : "The request could not finish. Refresh the saved statement before retrying.",
        );
        error.httpStatus = response.status;
        throw error;
      }
      return response;
    } finally {
      clearTimeout(timer);
      requests.delete(controller);
    }
  }
  function closeHelp(restoreFocus = true) {
    recoveryVersion += 1;
    recoveryBusy = false;
    el("help-panel").hidden = true;
    el("help-link").setAttribute("aria-expanded", "false");
    for (const id of ["example-outage", "example-hostile", "example-retry"])
      el(id).disabled = false;
    if (restoreFocus && capabilities)
      (el("help-link").getClientRects().length
        ? el("help-link")
        : document.querySelector("#profile-menu > summary")
      ).focus();
  }
  function openHelp() {
    if (!capabilities) return;
    el("help-panel").hidden = false;
    el("help-link").setAttribute("aria-expanded", "true");
    el("help-panel").focus();
  }
  el("help-link").addEventListener("click", () =>
    el("help-panel").hidden ? openHelp() : closeHelp(),
  );
  el("help-close").addEventListener("click", () => closeHelp());
  el("help-menu-link").addEventListener("click", () => {
    el("profile-menu").open = false;
    openHelp();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el("help-panel").hidden) closeHelp();
  });
  async function runRecovery(name, step) {
    if (recoveryBusy || !capabilities) return;
    const session = epoch,
      version = ++recoveryVersion;
    recoveryBusy = true;
    for (const id of ["example-outage", "example-hostile", "example-retry"])
      el(id).disabled = true;
    el("recovery-feedback").textContent = "Running the isolated example…";
    try {
      const result = await (
        await request("/workspace/recovery-example", { name, step })
      ).json();
      if (session !== epoch || version !== recoveryVersion) return;
      recoveryState = result.state;
      el("recovery-result-title").textContent = result.title;
      el("recovery-message").textContent = result.message;
      el("recovery-source-note").textContent = result.source_note || "";
      el("recovery-source-note").hidden = !result.source_note;
      el("recovery-events").replaceChildren();
      for (const text of result.events) {
        const item = document.createElement("li");
        item.textContent = text;
        el("recovery-events").append(item);
      }
      el("recovery-preserved").textContent = result.evidence_preserved
        ? "✓ Original evidence preserved"
        : "Evidence check failed; stop and inspect the example.";
      el("recovery-limits").textContent = result.limits;
      el("example-retry").hidden =
        name !== "finance_outage" || result.state !== "provider_error";
      el("recovery-result").hidden = false;
      el("recovery-feedback").textContent =
        "Example complete. Your workspace records were not changed.";
      document.dispatchEvent(
        new CustomEvent("workspace-recovery-finished", {
          detail: { state: result.state },
        }),
      );
      return result;
    } catch (error) {
      if (session !== epoch || version !== recoveryVersion) return;
      if (passwordMode && error.httpStatus === 401) {
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
        return;
      }
      el("recovery-feedback").textContent =
        "The example could not finish. Your saved statements are unchanged; try again shortly.";
    } finally {
      if (session === epoch && version === recoveryVersion) {
        recoveryBusy = false;
        for (const id of ["example-outage", "example-hostile", "example-retry"])
          el(id).disabled = false;
      }
    }
  }
  el("example-outage").addEventListener("click", () =>
    runRecovery("finance_outage", "start"),
  );
  el("example-hostile").addEventListener("click", () =>
    runRecovery("hostile_note", "start"),
  );
  el("example-retry").addEventListener("click", () =>
    runRecovery("finance_outage", "retry"),
  );
  function questionFeedback(message, error = false) {
    el("question-feedback").textContent = message;
    el("question-feedback").dataset.tone = error ? "error" : "info";
  }
  function renderConversation(value) {
    questionRecords = value.questions;
    const list = el("conversation-list");
    list.replaceChildren();
    conversationPending = value.questions.some(
      (item) => item.status === "pending",
    );
    conversationLimit = value.questions.length >= 20;
    const messages = {
      provider_error:
        "The assistant could not finish. Your invoice records are unchanged.",
      timeout:
        "The assistant timed out. The saved evidence is still available.",
      invalid_answer:
        "The answer could not be verified against the saved evidence. No unchecked answer is shown.",
      call_limit:
        "The question reached its request limit. Try a more specific question.",
      allowance_exhausted:
        "The workspace daily model allowance is reached. Saved evidence remains available; try again tomorrow.",
      rate_limited: "Wait five seconds before asking another question.",
    };
    for (const record of value.questions) {
      const item = document.createElement("li"),
        question = document.createElement("p");
      question.className = "saved-question";
      question.textContent = record.question;
      item.append(question);
      if (record.status === "pending" || record.status === "uncertain") {
        const note = document.createElement("p");
        note.className = "answer-status";
        note.textContent =
          record.status === "pending"
            ? "Checking the saved evidence…"
            : "The outcome of this request is unconfirmed. It will not be restarted automatically.";
        item.append(note);
      } else {
        const outcome = record.outcome;
        if (outcome.status !== "complete") {
          const note = document.createElement("p");
          note.className = "answer-status";
          note.textContent =
            messages[outcome.status] ||
            "The answer is unavailable. Your evidence is unchanged.";
          item.append(note);
        } else
          for (const block of outcome.blocks) {
            const text = document.createElement("p");
            text.className = "answer-text";
            text.textContent = block.text;
            item.append(text);
            for (const evidenceId of block.evidence_ids || []) {
              const row = saved.result.rows.find(
                (entry) => entry.id === evidenceId,
              );
              if (!row || row.invoice_ref !== block.invoice_ref) continue;
              const citation = document.createElement("button");
              citation.type = "button";
              citation.className = "evidence-citation";
              citation.textContent = `View ${row.invoice_ref} evidence`;
              citation.dataset.invoiceRef = row.invoice_ref;
              citation.addEventListener("click", async () => {
                if (!saved || !evidenceToken) return;
                const session = epoch,
                  reference = runId;
                try {
                  await openInvoiceEvidence(row.invoice_ref);
                  if (session === epoch && reference === runId)
                    guideEvent("workspace-citation-opened", {
                      reference,
                      invoiceRef: row.invoice_ref,
                    });
                } catch {
                  questionFeedback(
                    "The source view could not open. Refresh the statement, or search for this invoice in its evidence table.",
                    true,
                  );
                }
              });
              item.append(citation);
            }
          }
        const usage = document.createElement("span");
        usage.className = "answer-usage";
        usage.textContent = `${outcome.model_calls} model requests${outcome.reported_tokens == null ? "" : ` · ${outcome.reported_tokens} reported tokens`}`;
        item.append(usage);
      }
      list.append(item);
    }
    questionFeedback(
      conversationLimit
        ? "This statement has reached its 20-question limit. Choose New review to continue; earlier answers stay saved."
        : conversationPending
          ? "A question is being processed. Reloading will check its outcome without starting another request."
          : value.questions.length
            ? "Answers are saved with this statement. Existing reviews stay unchanged."
            : "Ask a question to see an explanation with source evidence.",
    );
    controls();
  }
  async function refreshConversation(session, id) {
    clearTimeout(conversationPoll);
    try {
      const value = await (await request(`/runs/${id}/conversation`)).json();
      if (session !== epoch || runId !== id || !saved) return;
      renderConversation(value);
      if (conversationPending)
        conversationPoll = setTimeout(
          () => refreshConversation(session, id),
          2000,
        );
    } catch (error) {
      if (session !== epoch || runId !== id) return;
      if (passwordMode && error.httpStatus === 401) {
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
        return;
      }
      questionFeedback(
        "Saved questions are temporarily unavailable. Your statement evidence is still open. Refresh to check again.",
        true,
      );
    }
  }
  async function waitForWorkspace(check, timeout = 25000) {
    const deadline = performance.now() + timeout;
    while (!check()) {
      if (!capabilities)
        throw new Error(
          "The workspace has closed. Sign in again to restart the tour.",
        );
      if (performance.now() >= deadline)
        throw new Error(
          "This step did not become ready. Your saved work is safe; retry or finish the tour.",
        );
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
  }
  async function openInvoiceEvidence(invoiceRef, { open = true } = {}) {
    const token = evidenceToken;
    await waitForWorkspace(
      () => evidenceReady || token !== evidenceToken,
      5000,
    );
    if (
      !saved ||
      token !== evidenceToken ||
      !saved.result.rows.some((row) => row.invoice_ref === invoiceRef)
    )
      throw new Error("The selected statement changed.");
    openedEvidence = "";
    focusedEvidence = "";
    el("evidence-frame").scrollIntoView({
      block: "start",
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
    // An opaque srcdoc needs a wildcard delivery target; both receivers validate source, origin and token.
    el("evidence-frame").contentWindow.postMessage(
      {
        type: open ? "open-invoice-evidence" : "focus-invoice",
        token,
        invoiceRef,
      },
      "*",
    );
    questionFeedback(`Opening evidence for ${invoiceRef}…`);
    await waitForWorkspace(
      () =>
        (open ? openedEvidence : focusedEvidence) === invoiceRef ||
        token !== evidenceToken,
      5000,
    );
    if (token !== evidenceToken)
      throw new Error("The selected statement changed.");
  }
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (
      event.source !== el("evidence-frame").contentWindow ||
      event.origin !== "null" ||
      !data ||
      typeof data !== "object" ||
      !evidenceToken ||
      data.token !== evidenceToken
    )
      return;
    if (data.type === "evidence-ready" && Object.keys(data).length === 2) {
      evidenceReady = true;
      return;
    }
    if (data.type === "evidence-escape" && Object.keys(data).length === 2) {
      if (walkthrough.isActive()) walkthrough.stop("dismissed");
      return;
    }
    if (
      Object.keys(data).length !== 3 ||
      !["invoice-focused", "invoice-evidence-opened", "ask-invoice"].includes(
        data.type,
      ) ||
      !saved?.result.rows.some((row) => row.invoice_ref === data.invoiceRef)
    )
      return;
    if (data.type === "invoice-focused") {
      focusedEvidence = data.invoiceRef;
      return;
    }
    if (data.type === "ask-invoice") {
      if (busy || !casework.showStatement()) return;
      selectTab("assistant");
      el("question-input").value =
        `Why does ${data.invoiceRef} differ, and what should I check next?`;
      el("question-input").focus();
      guideEvent("workspace-invoice-question", {
        reference: runId,
        invoiceRef: data.invoiceRef,
      });
      return;
    }
    openedEvidence = data.invoiceRef;
    questionFeedback(`Showing saved evidence for ${data.invoiceRef}.`);
    guideEvent("workspace-evidence-opened", {
      reference: runId,
      invoiceRef: data.invoiceRef,
    });
  });
  el("suggest-invoice").addEventListener("click", () => {
    const row = saved?.result.rows.find((entry) => entry.status !== "matched");
    el("question-input").value = row
      ? `Why does ${row.invoice_ref} differ, and what should I check next?`
      : "Summarise this statement.";
    el("question-input").focus();
  });
  el("suggest-priority").addEventListener("click", () => {
    el("question-input").value =
      "Which differences should I check first, and why?";
    el("question-input").focus();
  });
  el("question-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (
      busy ||
      !saved ||
      conversationPending ||
      !el("question-input").value.trim()
    )
      return;
    const session = epoch,
      id = runId,
      text = el("question-input").value.trim();
    if (
      !questionRequest ||
      questionRequest.id !== id ||
      questionRequest.text !== text
    )
      questionRequest = { id, text, requestId: crypto.randomUUID() };
    busy = true;
    controls();
    questionFeedback("Reading the statement evidence…");
    let guideOutcome = null;
    guideEvent("workspace-question-started", { reference: id });
    try {
      const result = await (
        await request(
          `/runs/${id}/questions`,
          { request_id: questionRequest.requestId, question: text },
          85000,
        )
      ).json();
      if (session !== epoch || runId !== id) return;
      if (result.question.status === "finished") {
        questionRequest = null;
        guideOutcome = result.question.outcome;
      }
      await refreshConversation(session, id);
      el("question-input").value = "";
    } catch (error) {
      if (session !== epoch || runId !== id) return;
      if (passwordMode && error.httpStatus === 401) {
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
        return;
      }
      questionFeedback(
        error.name === "AbortError"
          ? "The outcome is unconfirmed. Refresh saved questions before retrying; this request will not be started twice."
          : error.message,
        true,
      );
    } finally {
      if (session === epoch) {
        busy = false;
        controls();
        if (id === runId)
          guideEvent("workspace-question-finished", {
            reference: id,
            complete: guideOutcome?.status === "complete",
            invoiceRefs: (guideOutcome?.blocks || [])
              .filter((block) =>
                (block.evidence_ids || []).some((id) =>
                  saved?.result.rows.some(
                    (row) =>
                      row.id === id && row.invoice_ref === block.invoice_ref,
                  ),
                ),
              )
              .map((block) => block.invoice_ref),
          });
      }
    }
  });
  function importFeedback(message, error = false) {
    el("import-feedback").textContent = message;
    el("import-feedback").dataset.tone = error ? "error" : "info";
  }
  function clearImport() {
    pendingScenario = "";
    importVersion += 1;
    importRaw = null;
    importPreview = null;
    importRequestId = "";
    el("statement-file").value = "";
    el("import-preview").hidden = true;
    el("import-rows").replaceChildren();
    el("statement-drop").classList.remove("drag-active");
    importFeedback(
      "Choose a file to preview it. Previewing does not use Gemini or save a statement.",
    );
  }
  async function previewFile(file, session) {
    clearImport();
    const version = importVersion;
    if (!file || !/\.csv$/i.test(file.name))
      throw new Error(
        "Choose a CSV file, such as the downloadable Mallorca sample.",
      );
    if (file.size > 1000000)
      throw new Error("CSV exceeds the 1 MB upload limit.");
    const raw = new Uint8Array(await file.arrayBuffer());
    if (session !== epoch || version !== importVersion) return;
    importRaw = raw;
    importFeedback("Checking your statement…");
    const preview = await (
      await request("/statements/preview", raw, 20000, {
        raw: true,
        headers: { "Content-Type": "text/csv" },
      })
    ).json();
    if (session !== epoch || version !== importVersion) return;
    importPreview = preview;
    importRequestId = crypto.randomUUID();
    el("import-row-count").textContent = `${preview.row_count} rows`;
    el("import-preview-title").textContent = preview.statement_id;
    const differences = Object.entries(preview.counts).reduce(
      (n, [key, count]) => n + (key === "matched" ? 0 : count),
      0,
    );
    el("import-summary").textContent =
      `${preview.counts.matched || 0} matched · ${differences} differences · cutoff ${displayDate(preview.cutoff_date)}`;
    for (const row of preview.preview_rows) {
      const tr = document.createElement("tr");
      for (const value of [row.invoice_ref, row.currency, row.claimed_amount]) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      }
      el("import-rows").append(tr);
    }
    el("import-preview").hidden = false;
    importFeedback(
      "File checked. Confirm below to save and reconcile it. No model request has been made.",
    );
    document.dispatchEvent(new Event("workspace-statement-previewed"));
  }
  el("statement-file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) operate((session) => previewFile(file, session), true);
  });
  const drop = el("statement-drop");
  for (const name of ["dragenter", "dragover"])
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      if (!busy && capabilities?.permissions.create)
        drop.classList.add("drag-active");
    });
  for (const name of ["dragleave", "drop"])
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.remove("drag-active");
    });
  drop.addEventListener("drop", (event) => {
    if (busy || !capabilities?.permissions.create) return;
    const files = event.dataTransfer.files;
    if (files.length !== 1) {
      importFeedback("Choose one CSV statement at a time.", true);
      return;
    }
    operate((session) => previewFile(files[0], session), true);
  });
  el("sample-scenario").addEventListener("change", () => {
    if (pendingScenario) pendingScenario = experience.selected();
  });
  el("use-sample").addEventListener("click", () =>
    operate(async (session) => {
      const choice = experience.selected();
      const sample = await (await request("/statements/sample")).blob();
      if (session !== epoch) return;
      await previewFile(
        new File([sample], "mallorca-hotel-statement.csv", {
          type: "text/csv",
        }),
        session,
      );
      if (session === epoch && importPreview) pendingScenario = choice;
    }, true),
  );
  el("download-sample").addEventListener("click", () =>
    operate(async (session) => {
      const sample = await (await request("/statements/sample")).blob();
      if (session !== epoch) return;
      const url = URL.createObjectURL(sample),
        link = document.createElement("a");
      link.href = url;
      link.download = "mallorca-hotel-statement.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      importFeedback(
        "Sample downloaded. You can edit its invoice amounts, then upload it here.",
      );
    }, true),
  );
  el("confirm-import").addEventListener("click", () =>
    operate(async (session) => {
      if (!importRaw || !importPreview) return;
      const meta = await (
        await request("/statements/import", importRaw, 30000, {
          raw: true,
          headers: {
            "Content-Type": "text/csv",
            "X-Request-ID": importRequestId,
            "X-Statement-SHA256": importPreview.digest,
          },
        })
      ).json();
      if (session !== epoch) return;
      clearTimeout(pollTimer);
      newReviewRequestId = "";
      await load(meta.run_id, session);
      if (session !== epoch) return;
      const scenario = pendingScenario;
      clearImport();
      el("statement-import").open = false;
      if (scenario) experience.activate(scenario);
      status(
        "Statement saved",
        "Your uploaded statement has been compared with the sample finance ledger. Ask the assistant to investigate its differences.",
        "success",
      );
      document.dispatchEvent(new Event("workspace-statement-confirmed"));
    }, true),
  );
  function clearView() {
    casework.reset();
    replies.reset();
    comparison.reset();
    experience.reset();
    decisions.reset();
    questionRecords = [];
    clearTimeout(conversationPoll);
    conversationPending = false;
    conversationLimit = false;
    questionRequest = null;
    evidenceToken = "";
    evidenceReady = false;
    openedEvidence = "";
    el("conversation-list").replaceChildren();
    el("question-input").value = "";
    questionFeedback("Open a statement to ask a question.");
    saved = null;
    el("query-feedback").hidden = true;
    el("copy-fallback").hidden = true;
    el("copy-fallback").textContent = "";
    el("evidence-frame").hidden = true;
    el("evidence-frame").removeAttribute("srcdoc");
    el("report-empty").hidden = false;
    el("review-confirm").checked = false;
    el("query-text").textContent = "Open a statement to view its email draft.";
    el("query-source-text").textContent = "";
    el("query-source").open = false;
    el("activity-list").replaceChildren();
    for (const id of [
      "matched-count",
      "exception-count",
      "ledger-count",
      "cutoff-date",
    ])
      el(id).textContent = "—";
    el("query-dot").hidden = true;
    el("query-state").textContent = "Not ready";
    el("query-state").dataset.state = "";
    el("query-caption").textContent = "Open a statement to view its query.";
    el("review-note").textContent =
      "A finance reviewer checks the invoice evidence and complete query.";
    el("supplier-name").textContent = "Supplier statement";
    el("run-description").textContent = "Open a statement to begin.";
    for (const id of [
      "detail-reference",
      "detail-model",
      "detail-calls",
      "detail-tokens",
      "detail-revision",
    ])
      el(id).textContent = "—";
    el("statement-state").textContent = "Not opened";
    el("statement-state").dataset.state = "";
    el("assistant-state").textContent = "Ready when you are";
    el("assistant-state").dataset.state = "";
    el("assistant-headline").textContent = "Make sense of the differences.";
    el("assistant-summary").textContent =
      "Open a statement to see what needs checking.";
    step("step-records", false, "Waiting");
    step("step-investigate", false, "Waiting");
    step("step-query", false, "Waiting");
  }
  function resetConnection() {
    epoch += 1;
    clearTimeout(pollTimer);
    clearImport();
    closeHelp(false);
    walkthrough.stop(null);
    el("recovery-result").hidden = true;
    el("recovery-events").replaceChildren();
    el("recovery-source-note").textContent = "";
    el("recovery-feedback").textContent = "Choose an example to begin.";
    recoveryState = "";
    for (const controller of requests) controller.abort();
    pass = "";
    capabilities = null;
    busy = false;
    investigating = false;
    newReviewRequestId = "";
    el("access-pass").value = "";
    el("reviewer-name").value = "";
    el("access-summary").textContent = "Not connected";
    document.querySelector(".sample-label").textContent = "Sample data";
    clearView();
    controls();
  }
  function activity() {
    const labels = {
      get_reconciliation: "Compared the statement and ledger",
      get_exception: "Read invoice source evidence",
      lookup_payable: "Checked an invoice against the finance service",
      propose_query: "Prepared the factual supplier query",
    };
    const list = el("activity-list");
    list.replaceChildren();
    const events = saved.meta.events || [];
    for (const event of events) {
      let text;
      if (event.event === "tool_call")
        text = labels[event.tool] || "Used an invoice tool";
      else if (event.event === "review_started")
        text = "Started a new review; previous reviews remain saved";
      else if (event.event === "model_attempt")
        text = `Model request ${event.attempt}`;
      else if (event.event === "provider_error")
        text =
          event.http_status === 429
            ? "Model request rate-limited by Google"
            : "Model request could not complete";
      else if (event.event === "transient_retry")
        text = `Waiting ${event.delay_seconds ?? "briefly"} seconds before retrying`;
      else if (event.event === "retry_deferred")
        text = "Stopped: the provider wait exceeded the run deadline";
      else if (event.event === "drafting_finished")
        text = complete()
          ? "Query saved for human review"
          : "Investigation ended; invoice evidence preserved";
      if (text) {
        const item = document.createElement("li");
        item.textContent = text;
        list.append(item);
      }
    }
    if (!list.children.length) {
      const item = document.createElement("li");
      item.textContent =
        "Records reconciled. No agent investigation recorded yet.";
      list.append(item);
    }
    if (saved.meta.review) {
      const item = document.createElement("li");
      item.textContent = `Review recorded by ${saved.meta.review.reviewer}`;
      list.append(item);
    }
    el("detail-reference").textContent = runId;
    el("detail-model").textContent =
      saved.meta.served_model_version || saved.meta.model || "Not used";
    el("detail-calls").textContent = String(saved.meta.model_calls ?? 0);
    el("detail-tokens").textContent =
      saved.meta.total_tokens == null
        ? "Not reported"
        : String(saved.meta.total_tokens);
    el("detail-revision").textContent = String(saved.revision);
  }
  function describe() {
    const frozen = Boolean(saved.meta.review),
      done = complete(),
      state = saved.meta.drafting_status;
    const throttled = saved.meta.events?.some(
      (event) => event.event === "provider_error" && event.http_status === 429,
    );
    el("statement-state").textContent = frozen
      ? "Reviewed"
      : done
        ? "Ready for review"
        : "Needs review";
    el("statement-state").dataset.state = frozen
      ? "reviewed"
      : done
        ? "complete"
        : "attention";
    el("query-state").textContent = frozen
      ? "Reviewed"
      : done
        ? "Draft saved"
        : "Template";
    el("query-state").dataset.state = frozen
      ? "reviewed"
      : done
        ? "complete"
        : "attention";
    el("query-caption").textContent = done
      ? "Review the email, check its source evidence, then copy it into your email app. Nothing is sent automatically."
      : "This is the factual template. The assistant has not completed an investigation for this statement.";
    el("query-text").textContent = saved.supplier_email || saved.draft_text;
    el("query-source-text").textContent = saved.draft_text;
    el("query-dot").hidden = !done || frozen;
    el("review-note").textContent = frozen
      ? `Recorded by ${saved.meta.review.reviewer}. The saved evidence and query are read-only.`
      : capabilities?.permissions.review
        ? "Check every item, then record your review. This does not send a message or change an account."
        : "A finance reviewer records the check. Your analyst role can investigate and prepare the query.";
    step("step-records", true, "Done");
    step(
      "step-investigate",
      done,
      done ? "Done" : running() ? "Working" : "Waiting",
    );
    step("step-query", done, done ? "Done" : "Waiting");
    el("assistant-state").dataset.state =
      frozen || done ? "complete" : throttled ? "error" : "attention";
    if (frozen) {
      el("assistant-state").textContent = "Review complete";
      el("assistant-headline").textContent = "This statement has been checked.";
      el("assistant-summary").textContent =
        "The query and its supporting records are saved together. You can inspect the review in Supplier email or Activity.";
      status(
        "Review recorded",
        `Recorded by ${saved.meta.review.reviewer}. Nothing has been sent.`,
        "success",
      );
    } else if (running()) {
      el("assistant-state").textContent = "Investigating";
      el("assistant-headline").textContent = "Checking the invoice evidence.";
      el("assistant-summary").textContent =
        "The assistant is working on this statement. Its outcome will appear here when saved.";
      status(
        "Investigation in progress",
        "This statement is already being investigated. Another request cannot start while it runs.",
        "pending",
      );
    } else if (done) {
      el("assistant-state").textContent = "Query ready";
      el("assistant-headline").textContent =
        "Your email draft is ready to check.";
      el("assistant-summary").textContent =
        "The assistant has prepared a query covering the invoice differences. Open Supplier email to review the draft and its source evidence.";
      status(
        "Agent query saved",
        "Inspect each item before recording a review.",
        "success",
      );
    } else if (state === "offline_template") {
      const count =
        Object.values(saved.result.counts).reduce((n, v) => n + v, 0) -
        (saved.result.counts.matched || 0);
      el("assistant-state").textContent = "Ready to investigate";
      el("assistant-headline").textContent =
        `${count} differences worth a closer look.`;
      el("assistant-summary").textContent =
        "I can check the supporting invoices and turn these differences into a supplier query for you to review.";
      status(
        "Statement ready",
        "The records are reconciled. Use the assistant to investigate differences.",
      );
    } else {
      el("assistant-state").textContent = throttled
        ? "Model access limited"
        : "Needs attention";
      el("assistant-headline").textContent = "The invoice evidence is safe.";
      el("assistant-summary").textContent = throttled
        ? "Google's request limit stopped this investigation. A complete agent query was not saved. Wait for model access before retrying."
        : "The investigation did not complete. You can inspect the saved records and template query before deciding what to do next.";
      status(
        "Investigation not completed",
        el("assistant-summary").textContent,
        "error",
      );
    }
    activity();
    controls();
  }
  async function load(id, session) {
    if (!casework.allowLeave()) return false;
    if (!/^[a-f0-9]{20}$/.test(id))
      throw new Error("Enter the 20-character saved run reference.");
    const previousReference = saved?.meta.run_id || runId;
    clearView();
    const data = await (await request(`/runs/${id}`)).json();
    const report = await request(`/runs/${id}/evidence`),
      html = await report.text();
    if (session !== epoch) return;
    if (String(data.revision) !== report.headers.get("X-Run-Revision"))
      throw new Error(
        "The statement changed while loading. Refresh to open one consistent saved version.",
      );
    saved = data;
    runId = id;
    el("run-reference").value = id;
    el("comparison-current-ref").textContent = id;
    if (previousReference && previousReference !== id)
      comparison.setPrevious(previousReference);
    document.querySelector(".sample-label").textContent =
      data.meta.source_kind === "uploaded"
        ? "Uploaded · sample ledger"
        : "Sample data";
    // Presentation-only CSS: canonical saved HTML and financial evidence are unchanged.
    evidenceToken = crypto.randomUUID();
    el("evidence-frame").srcdoc = html
      .replace("</head>", `<style>${embeddedCss}</style></head>`)
      .replace(
        "<body>",
        `<body class="embedded" data-parent-origin="${location.origin}" data-evidence-token="${evidenceToken}">`,
      );
    el("evidence-frame").hidden = false;
    el("report-empty").hidden = true;
    const ledger = data.result.rows.flatMap((row) => row.ledger_lines)[0];
    el("supplier-name").textContent = ledger
      ? `${ledger.hotel} · ${ledger.destination}`
      : data.result.supplier_id;
    el("run-description").textContent =
      `Statement ${data.result.statement_id} · ${data.result.supplier_id}`;
    el("cutoff-date").textContent = displayDate(data.result.cutoff_date);
    el("matched-count").textContent = String(data.result.counts.matched || 0);
    el("exception-count").textContent = String(
      Object.values(data.result.counts).reduce((n, v) => n + v, 0) -
        (data.result.counts.matched || 0),
    );
    el("ledger-count").textContent = String(data.result.input_counts.ledger);
    el("evidence-description").textContent =
      "Search the invoices and open source evidence for each difference.";
    const firstDifference = data.result.rows.find(
      (row) => row.status !== "matched",
    );
    el("suggest-invoice").textContent = firstDifference
      ? `Explain ${firstDifference.invoice_ref}`
      : "Explain this statement";
    describe();
    await refreshConversation(session, id);
  }
  async function operate(action, preserveView = false) {
    if (!casework.allowLeave()) return;
    const session = epoch;
    busy = true;
    controls();
    try {
      await action(session);
    } catch (error) {
      if (session === epoch) {
        if (passwordMode && error.httpStatus === 401 && capabilities) {
          showPasswordGate(
            "Your session has ended. Enter the password to continue.",
          );
          return;
        }
        if (passwordMode && !capabilities) {
          showPasswordGate(error.message, "error");
          el("access-pass").focus();
          return;
        }
        if (!preserveView) clearView();
        else
          importFeedback(
            error.name === "AbortError"
              ? "The outcome is unconfirmed. Your file is retained; retry to check the saved result."
              : error.message,
            true,
          );
        if (!capabilities) {
          el("workspace-content").hidden = true;
          el("access-panel").hidden = false;
          el("access-form").hidden = localMode || identityMode;
          el("resume-local").hidden = !localMode && !identityMode;
        }
        const message = (
          error.name === "AbortError"
            ? "The request did not confirm its outcome"
            : error.message
        ).replace(/[. ]+$/, "");
        if (preserveView)
          status(
            "Check your statement",
            `${message}. Your current saved statement is unchanged.`,
            "error",
          );
        else
          status(
            "Action needs attention",
            `${message}. ${capabilities && runId ? "Refresh the saved statement before retrying." : "Open the workspace again to check your connection."}`,
            "error",
          );
      }
    } finally {
      if (session === epoch) {
        busy = false;
        controls();
        if (capabilities && saved) walkthrough.offer();
      }
    }
  }
  async function connected(session, preferredId = "") {
    const data = await (await request("/capabilities")).json();
    if (session !== epoch) return;
    capabilities = data;
    if (passwordMode) {
      document.querySelector(".app-header nav").hidden = false;
      el("profile-menu").hidden = false;
    }
    const role = passwordMode
      ? "Supplier workspace"
      : data.permissions.execute
        ? "Accounts analyst"
        : data.permissions.review
          ? "Finance reviewer"
          : "Read-only access";
    el("profile-name").textContent = role;
    document.querySelector(".avatar").textContent = passwordMode
      ? "SW"
      : data.permissions.execute
        ? "AA"
        : data.permissions.review
          ? "FR"
          : "RO";
    el("manage-access").hidden = localMode || identityMode || passwordMode;
    el("access-summary").textContent = `${role} · ${data.supplier_id}`;
    el("local-profiles").hidden = !localMode;
    el("reviewer-name").readOnly = identityMode;
    if (identityMode) el("reviewer-name").value = currentProfile?.email || "";
    el("access-panel").hidden = true;
    el("workspace-content").hidden = false;
    el("model-badge").hidden = data.model_mode !== "offline_integration";
    el("model-note").textContent =
      data.model_mode === "offline_integration"
        ? "This local workspace runs Google ADK with a sample model and sample finance records. It does not make a live Gemini request. Financial figures come from the saved records; you make the review decision."
        : "Google ADK connects Gemini to supplier-scoped invoice tools. Financial figures come from saved records; you make the review decision.";
    let id = preferredId || el("run-reference").value.trim();
    if (!id && data.permissions.create)
      id = (await (await request("/runs", {})).json()).run_id;
    if (id) await load(id, session);
    else {
      status(
        "Workspace open",
        "Open an existing statement reference from your profile menu.",
      );
      controls();
    }
  }
  async function profile(role) {
    const tab =
      document
        .querySelector("[role=tab][aria-selected=true]")
        ?.id?.replace("tab-", "") || "assistant";
    resetConnection();
    await operate(async (session) => {
      status(
        "Opening workspace",
        "Loading your role and saved statement.",
        "pending",
      );
      const data = await (await request("/workspace/session", { role })).json();
      if (session !== epoch) return;
      currentProfile = data;
      localMode = true;
      await connected(session, runId || data.statement_ref || "");
      selectTab(tab);
      el("profile-menu").open = false;
    });
  }
  el("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (passwordMode) {
      const password = el("access-pass").value;
      if (!password) return;
      resetConnection();
      operate(async (session) => {
        status("Signing in", "Checking your password.", "pending");
        await request("/workspace/login", { password });
        scheduleExpiry();
        await connected(session);
        el("access-pass").value = "";
        el("main").focus({ preventScroll: true });
      });
      return;
    }
    const supplied = el("access-pass").value.trim();
    if (supplied.length < 16) {
      status(
        "Credential needed",
        "Enter your complete workspace credential.",
        "error",
      );
      return;
    }
    resetConnection();
    pass = supplied;
    operate(async (session) => {
      status(
        "Opening workspace",
        "Checking your available actions.",
        "pending",
      );
      await connected(session);
      el("access-pass").value = "";
    });
  });
  el("manage-access").addEventListener("click", () => {
    el("access-panel").hidden = false;
    el("access-form").hidden = localMode || identityMode;
    el("resume-local").hidden = !localMode && !identityMode;
    el("profile-menu").open = false;
    el("access-panel").scrollIntoView({ block: "start", behavior: "smooth" });
  });
  el("resume-local").addEventListener("click", () =>
    identityMode ? boot() : profile(currentProfile?.role || "analyst"),
  );
  el("role-analyst").addEventListener("click", () => profile("analyst"));
  el("role-reviewer").addEventListener("click", () => profile("reviewer"));
  el("switch-reviewer").addEventListener("click", () => profile("reviewer"));
  el("disconnect").addEventListener("click", async () => {
    if (passwordMode) {
      if (busy) return;
      busy = true;
      controls();
      try {
        await request("/workspace/logout", {});
        showPasswordGate("You have signed out.");
      } catch (error) {
        status(
          "Sign out not confirmed",
          "The service could not cancel your session. Try signing out again when it is available.",
          "error",
        );
      } finally {
        busy = false;
        controls();
      }
      return;
    }
    let confirmed = true;
    try {
      if (localMode) await request("/workspace/session", { role: "signout" });
    } catch {
      confirmed = false;
    } finally {
      resetConnection();
      el("workspace-content").hidden = true;
      el("access-panel").hidden = false;
      el("access-form").hidden = localMode || identityMode;
      el("resume-local").hidden = !localMode && !identityMode;
      el("profile-name").textContent = "Workspace closed";
      el("profile-menu").open = false;
      status(
        "Workspace closed",
        confirmed
          ? identityMode
            ? "Displayed records have been cleared. Your Google sign-in remains active in this browser."
            : "Displayed records and your workspace session have been cleared."
          : "Displayed records were cleared, but session closure was not confirmed. Close this tab or reopen the workspace when the service is available.",
        confirmed ? "info" : "error",
      );
    }
  });
  el("new-review").addEventListener("click", () =>
    operate(async (session) => {
      newReviewRequestId ||= crypto.randomUUID();
      status("Opening new review", "Previous reviews remain saved.", "pending");
      const meta = await (
        await request(`/runs/${runId}/new-review`, {
          request_id: newReviewRequestId,
        })
      ).json();
      if (session === epoch) {
        newReviewRequestId = "";
        runId = meta.run_id;
        await load(runId, session);
        selectTab("assistant");
      }
    }),
  );
  el("create-run").addEventListener("click", () =>
    operate(async (session) => {
      status(
        "Opening statement",
        "Comparing the statement with its saved ledger balances.",
        "pending",
      );
      const meta = await (await request("/runs", {})).json();
      if (session === epoch) {
        runId = meta.run_id;
        await load(runId, session);
      }
    }),
  );
  el("open-form").addEventListener("submit", (event) => {
    event.preventDefault();
    el("profile-menu").open = false;
    operate(async (session) => {
      const id = el("run-reference").value.trim();
      if (!/^[a-f0-9]{20}$/.test(id))
        throw new Error("Enter the 20-character saved run reference.");
      if (passwordMode) await request("/workspace/selection", { run_id: id });
      if (session === epoch) await load(id, session);
    });
  });
  el("reload-run").addEventListener("click", () =>
    operate((session) => load(runId, session)),
  );
  el("execute-run").addEventListener("click", () =>
    operate(async (session) => {
      const id = runId;
      investigating = true;
      controls();
      el("assistant-state").textContent = "Investigating";
      el("assistant-headline").textContent = "Checking the invoice evidence.";
      status(
        "Investigating differences",
        "The assistant is checking this statement. Its query will appear here when saved.",
        "pending",
      );
      const started = Date.now();
      let polling = true;
      async function poll() {
        if (session !== epoch || !polling || Date.now() - started > 140000)
          return;
        try {
          const data = await (await request(`/runs/${id}`)).json();
          if (session !== epoch || !polling) return;
          if (data.execution.status === "running")
            status(
              "Investigation in progress",
              "The statement is held for one investigation. Its evidence remains saved.",
              "pending",
            );
        } catch {
          if (session === epoch && polling)
            status(
              "Waiting for the saved outcome",
              "Progress could not be read. The investigation may still be running; refresh before starting another.",
              "pending",
            );
        }
        if (session === epoch && polling) pollTimer = setTimeout(poll, 4000);
      }
      pollTimer = setTimeout(poll, 3000);
      try {
        await request(
          `/runs/${id}/execute`,
          { request_id: crypto.randomUUID() },
          145000,
        );
      } finally {
        polling = false;
        investigating = false;
        clearTimeout(pollTimer);
      }
      if (session === epoch) {
        await load(id, session);
        if (complete()) selectTab("query");
      }
    }),
  );
  el("review-form").addEventListener("submit", (event) => {
    event.preventDefault();
    operate(async (session) => {
      status(
        "Recording review",
        "Saving your confirmation with this statement.",
        "pending",
      );
      await request(`/runs/${runId}/review`, {
        reviewer: el("reviewer-name").value.trim(),
      });
      if (session === epoch) await load(runId, session);
    });
  });
  for (const name of ["assistant", "query", "activity"]) {
    el(`tab-${name}`).addEventListener("click", () => {
      selectTab(name);
      if (name === "activity") casework.showTimeline();
    });
    el(`tab-${name}`).addEventListener("keydown", (event) => {
      const tabs = ["assistant", "query", "activity"],
        index = tabs.indexOf(name);
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        selectTab(
          tabs[(index + (event.key === "ArrowRight" ? 1 : 2)) % 3],
          true,
        );
      }
      if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        selectTab(event.key === "Home" ? "assistant" : "activity", true);
      }
    });
  }
  el("query-view-evidence").addEventListener("click", () => {
    selectTab("assistant");
    el("evidence-frame").scrollIntoView({ block: "start", behavior: "auto" });
  });
  el("show-query").addEventListener("click", () => selectTab("query", true));
  el("activity-link").addEventListener("click", () => {
    casework.showTimeline();
  });
  function queryFeedback(message) {
    el("query-feedback").textContent = message;
    el("query-feedback").hidden = false;
  }
  el("copy-query").addEventListener("click", async () => {
    if (!saved) return;
    const session = epoch,
      data = saved;
    try {
      await navigator.clipboard.writeText(
        data.supplier_email || data.draft_text,
      );
      if (session === epoch && saved === data)
        queryFeedback("Email draft copied.");
    } catch {
      if (session !== epoch || saved !== data) return;
      const fallback = el("copy-fallback");
      fallback.textContent = data.supplier_email || data.draft_text;
      fallback.hidden = false;
      const range = document.createRange();
      range.selectNodeContents(fallback);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      fallback.focus();
      queryFeedback(
        "Automatic copying is unavailable. The query is selected below; use your device's copy command.",
      );
    }
  });
  el("export-query").addEventListener("click", () => {
    if (!saved) return;
    const url = URL.createObjectURL(
      new Blob([saved.supplier_email || saved.draft_text], {
        type: "text/plain;charset=utf-8",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `supplier-query-${runId}.txt`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    queryFeedback("Email draft downloaded as a text file.");
  });
  window.addEventListener("pagehide", resetConnection);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && passwordMode) boot();
  });
  function showPasswordGate(message = "", tone = "info") {
    clearTimeout(expiryTimer);
    resetConnection();
    el("workspace-content").hidden = true;
    el("access-panel").hidden = false;
    el("access-form").hidden = false;
    el("resume-local").hidden = true;
    el("local-profiles").hidden = true;
    el("profile-menu").open = false;
    document.querySelector(".app-header nav").hidden = true;
    el("profile-menu").hidden = true;
    el("login-feedback").textContent = message;
    el("login-feedback").hidden = !message;
    el("login-feedback").dataset.tone = tone;
    el("profile-name").textContent = "Signed out";
    document.querySelector(".avatar").textContent = "SW";
    el("access-title").textContent = "Sign in to supplier operations";
    el("access-description").textContent =
      "Enter your password to open the workspace.";
    el("access-form").querySelector("label").textContent = "Password";
    el("access-pass").minLength = 1;
    el("access-pass").required = true;
    el("access-pass").autocomplete = "current-password";
    el("pass-note").textContent =
      "Use the password provided with your workspace link.";
    el("connect").textContent = "Sign in";
    el("disconnect").textContent = "Sign out";
    el("manage-access").hidden = true;
    el("show-password").hidden = false;
    el("access-pass").type = "password";
    el("show-password").textContent = "Show password";
    el("show-password").setAttribute("aria-pressed", "false");
    status(
      "Workspace locked",
      message || "Enter the password provided with your link.",
    );
    controls();
  }
  function scheduleExpiry(seconds = 3600) {
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(
      () =>
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        ),
      Math.max(0, seconds) * 1000,
    );
  }
  el("show-password").addEventListener("click", () => {
    const visible = el("access-pass").type === "password";
    el("access-pass").type = visible ? "text" : "password";
    el("show-password").textContent = visible
      ? "Hide password"
      : "Show password";
    el("show-password").setAttribute("aria-pressed", String(visible));
  });
  const experience = experienceFactory({
    getState: () => ({
      reference: runId,
      saved,
      canEdit: Boolean(capabilities && saved && !busy),
    }),
    async openEvidence(invoice) {
      if (casework.showStatement()) await openInvoiceEvidence(invoice);
    },
    ask(invoice) {
      if (!casework.showStatement()) return;
      selectTab("assistant");
      el("question-input").value =
        `Why does ${invoice} differ, and what should I check next?`;
      el("question-form").scrollIntoView({
        block: "center",
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
      el("question-input").focus({ preventScroll: true });
      questionFeedback(
        "Your question is ready to edit. Press Ask assistant to make a Gemini request.",
      );
    },
    async followup(invoice) {
      const reference = runId;
      await casework.showFollowup({ invoiceRef: invoice });
      if (
        runId === reference &&
        !el("followup-view").hidden &&
        el("case-title").textContent === invoice
      )
        el("case-detail").scrollIntoView({
          block: "start",
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth",
        });
    },
    feedback: (message) => status("Evidence could not open", message, "error"),
  });
  const casework = caseworkFactory({
    describeOutcome: (row, entry) =>
      experience.outcome(row, entry, questionRecords),
    preferredInvoice: () => experience.invoice(),
    getState: () => ({
      connected: Boolean(capabilities && saved),
      reference: runId,
      epoch,
      saved,
      canEdit: Boolean(capabilities?.permissions.execute && !busy),
    }),
    read: async (path) => (await request(path, undefined, 45000)).json(),
    write: async (path, body) => (await request(path, body, 45000)).json(),
    openEvidence: openInvoiceEvidence,
    selectActivity: () => selectTab("activity"),
    expired(error) {
      if (passwordMode && error.httpStatus === 401)
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
    },
    allowExtraLeave: () => replies.allowLeave() && decisions.allowLeave(),
    feedback: (message) => status("Check the saved evidence", message, "error"),
  });
  const replies = repliesFactory({
    getState: () => ({
      connected: Boolean(capabilities && saved),
      reference: runId,
      epoch,
      canEdit: Boolean(capabilities?.permissions.execute && !busy),
      modelEnabled: Boolean(capabilities?.replies_enabled),
    }),
    read: async (path) => (await request(path, undefined, 45000)).json(),
    write: async (path, body, timeout) =>
      (await request(path, body, timeout)).json(),
    setBusy(value) {
      busy = value;
      controls();
    },
    onRecorded(board) {
      casework.invalidateCases();
      el("followup-count").textContent = String(
        board.rows.filter((row) => board.cases[row.id]?.status !== "resolved")
          .length,
      );
    },
    isVisible: () => !el("responses-view").hidden,
    expired(error) {
      if (passwordMode && error.httpStatus === 401)
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
    },
    openEvidence(invoice) {
      if (casework.showStatement())
        openInvoiceEvidence(invoice).catch((error) =>
          status("Evidence could not open", error.message, "error"),
        );
    },
  });
  const comparison = comparisonFactory({
    getState: () => ({
      connected: Boolean(capabilities && saved && !busy),
      reference: runId,
      epoch,
    }),
    read: async (path) => (await request(path, undefined, 45000)).json(),
    expired(error) {
      if (passwordMode && error.httpStatus === 401)
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
    },
  });
  const decisions = decisionsFactory({
    getState: () => ({
      connected: Boolean(capabilities && saved),
      reference: runId,
      epoch,
      canEdit: Boolean(capabilities?.permissions.execute && !busy),
      enabled: Boolean(capabilities?.decisions_enabled),
    }),
    read: async (path) => (await request(path, undefined, 45000)).json(),
    write: async (path, body) => (await request(path, body, 110000)).json(),
    setBusy(value) {
      busy = value;
      controls();
    },
    allowStart: () => casework.allowLeave(),
    expired(error) {
      if (passwordMode && error.httpStatus === 401)
        showPasswordGate(
          "Your session has ended. Enter the password to continue.",
        );
    },
  });
  function showDecisions() {
    if (casework.showView("decisions")) {
      closeHelp(false);
      decisions.load();
      el("decisions-view").focus({ preventScroll: true });
    }
  }
  el("tour-discover-decisions").addEventListener("click", () => {
    walkthrough.stop("completed");
    showDecisions();
  });
  for (const name of ["responses", "comparison", "about", "decisions"])
    el(`view-${name}`).addEventListener("click", () => {
      if (!casework.showView(name)) return;
      closeHelp(false);
      if (name === "responses") replies.load();
      if (name === "decisions") decisions.load();
      el(`${name}-view`).focus({ preventScroll: true });
    });
  el("copy-statement-reference").addEventListener("click", async () => {
    if (!runId) return;
    try {
      await navigator.clipboard.writeText(runId);
      el("comparison-feedback").textContent = "Statement reference copied.";
    } catch {
      el("comparison-feedback").textContent =
        "Copy the visible statement reference above using your device’s copy command.";
    }
  });
  const walkthrough = onboardingFactory({
    getState: () => ({
      connected: Boolean(
        capabilities && saved && capabilities.permissions.create,
      ),
      reference: runId,
      sourceKind: saved?.meta.source_kind || "fixture",
      hasPreview: Boolean(importPreview),
      recoveryState,
      busy,
      invoiceRef:
        experience.invoice() ||
        saved?.result.rows.find((row) => row.status !== "matched")?.invoice_ref,
      questionsEnabled: Boolean(
        capabilities?.questions_enabled && capabilities?.permissions.execute,
      ),
      needsConsent: Boolean(
        importRaw ||
          (saved &&
            (saved.meta.source_kind === "uploaded" ||
              saved.meta.review ||
              complete())),
      ),
    }),
    showUpload() {
      if (!casework.showStatement())
        throw new Error(
          "Your unsaved follow-up note is still open. Save it or discard it before starting this task.",
        );
      closeHelp(false);
      el("statement-import").open = true;
    },
    closeHelp: () => closeHelp(false),
    openHelp: () => openHelp(),
    showStatement: () => casework.showStatement(),
    showFollowup: () => {
      closeHelp(false);
      return casework.showFollowup({ invoiceRef: experience.invoice() });
    },
    showQuality: () => {
      closeHelp(false);
      return casework.showQuality();
    },
    showTimeline: () => {
      closeHelp(false);
      return casework.showTimeline();
    },
    async prepareSample() {
      if (busy)
        throw new Error(
          "Wait for the current action to finish before loading the sample.",
        );
      el("statement-import").open = true;
      el("use-sample").click();
      await waitForWorkspace(() => !busy);
      if (!importPreview) throw new Error(el("import-feedback").textContent);
    },
    async confirmSample() {
      if (busy || !importPreview)
        throw new Error("Preview the sample before confirming it.");
      const previous = runId;
      el("confirm-import").click();
      await waitForWorkspace(() => !busy);
      if (!saved || runId === previous)
        throw new Error(
          "The sample was not confirmed. Check the upload message and retry.",
        );
      return runId;
    },
    discardPreview() {
      clearImport();
      el("statement-import").open = false;
    },
    findCitation: (invoice) =>
      [...document.querySelectorAll(".evidence-citation")]
        .reverse()
        .find((node) => node.dataset.invoiceRef === invoice)
        ?.closest("li"),
    async focusGuideInvoice() {
      if (!casework.showStatement())
        throw new Error("Save or discard the open follow-up note first.");
      closeHelp(false);
      const row =
        saved?.result.rows.find(
          (item) => item.invoice_ref === experience.invoice(),
        ) || saved?.result.rows.find((item) => item.status !== "matched");
      if (!row) throw new Error("No difference is available to check.");
      await openInvoiceEvidence(row.invoice_ref, { open: false });
    },
    async openFirstEvidence() {
      const row =
        saved?.result.rows.find(
          (item) => item.invoice_ref === experience.invoice(),
        ) || saved?.result.rows.find((item) => item.status !== "matched");
      if (!row) throw new Error("No difference is available to open.");
      await openInvoiceEvidence(row.invoice_ref);
    },
    showQuery() {
      casework.showStatement();
      closeHelp(false);
      selectTab("query");
    },
    showAssistant() {
      casework.showStatement();
      closeHelp(false);
      selectTab("assistant");
    },
    async runOutage() {
      const result = await runRecovery("finance_outage", "start");
      if (result?.state !== "provider_error")
        throw new Error(
          "The recovery example could not start. You can retry or finish the tour.",
        );
    },
    async retryOutage() {
      const result = await runRecovery("finance_outage", "retry");
      if (result?.state !== "recovered")
        throw new Error(
          "The recovery example did not finish. Your saved statement is unchanged.",
        );
    },
    prepareFirstQuestion() {
      el("suggest-invoice").click();
      questionFeedback(
        "Your question is ready. Choose Ask assistant to run it against the saved evidence.",
      );
    },
    focusQuestion() {
      el("question-input").focus();
    },
  });
  el("start-tour").addEventListener("click", () => walkthrough.start());
  async function boot() {
    controls();
    if (passwordMode) {
      showPasswordGate();
      el("access-panel").hidden = true;
      status(
        "Opening workspace",
        "Checking your session and saved statement.",
        "pending",
      );
      try {
        const data = await (await request("/workspace/session")).json();
        scheduleExpiry(data.expires_in);
        await operate((session) =>
          connected(session, data.statement_ref || ""),
        );
      } catch (error) {
        showPasswordGate(
          error.httpStatus === 401
            ? undefined
            : "Sign-in is temporarily unavailable. Try again shortly.",
        );
      }
      return;
    }
    if (identityMode) {
      el("access-title").textContent = "Google workspace access";
      el("access-description").textContent =
        "Your Google sign-in determines your assigned workspace role. Reopen this workspace after signing in or ask the workspace owner to check your access.";
      el("access-form").hidden = true;
    }
    try {
      const data = await (await request("/workspace/session")).json();
      if (!data.available) throw new Error("Local session unavailable");
      if (identityMode && data.mode !== "google")
        throw new Error("Google workspace session unavailable");
      localMode = !identityMode;
      currentProfile = data;
      if (data.role)
        await operate((session) =>
          connected(session, data.statement_ref || ""),
        );
      else await profile("analyst");
    } catch (error) {
      if (identityMode) {
        el("access-panel").hidden = false;
        el("access-form").hidden = true;
        el("resume-local").hidden = false;
        const denied = error.httpStatus === 401 || error.httpStatus === 403;
        el("access-title").textContent = denied
          ? "Google workspace access"
          : "Workspace unavailable";
        el("access-description").textContent = denied
          ? "Your signed-in account could not open an assigned workspace role. Ask the owner to check your access."
          : "The workspace service could not load your session or saved statement. Reopen it when the service is available.";
        status(
          denied ? "Google workspace access needed" : "Workspace unavailable",
          el("access-description").textContent,
          "error",
        );
        return;
      }
      if (localMode) {
        status(
          "Workspace unavailable",
          "The local session could not be opened. Reload this page to try again.",
          "error",
        );
        return;
      }
      el("access-panel").hidden = false;
      el("access-pass").required = true;
      status(
        "Open your workspace",
        "Use your workspace credential to continue.",
      );
    }
  }
  boot();
})();

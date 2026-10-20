function createReplies(api) {
  const el = (id) => document.getElementById(id);
  let selectionVersion = 0,
    generation = 0,
    working = false,
    sourceDirty = false,
    actionDirty = false,
    request = null,
    chosen = null,
    boardGeneration = 0;
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const state = () => api.getState();
  const valid = (s, v) =>
    v === generation &&
    s.epoch === state().epoch &&
    s.reference === state().reference &&
    state().connected;
  const message = (text, error = false) => {
    el("reply-feedback").textContent = text;
    el("reply-feedback").dataset.tone = error ? "error" : "info";
  };
  function syncControls() {
    const s = state();
    for (const id of [
      "reply-text",
      "reply-file",
      "use-reply-sample",
      "reply-filename",
    ])
      el(id).disabled = working || !s.canEdit;
    el("analyse-reply").disabled = working || !s.canEdit || !s.modelEnabled;
    for (const button of el("reply-list").querySelectorAll(".reply-review"))
      button.disabled =
        working || !s.canEdit || button.dataset.applied === "true";
    el("refresh-replies").disabled = working || !s.connected;
    for (const id of [
      "reply-actor",
      "reply-note",
      "reply-status",
      "confirm-reply-action",
    ])
      el(id).disabled = working || !s.canEdit || !chosen;
  }
  function allowLeave() {
    if (working) {
      message("Wait for the reply action to finish before leaving.");
      return false;
    }
    if (
      (sourceDirty || actionDirty) &&
      !window.confirm(
        "Discard your unsaved hotel reply or follow-up note? Saved sources and history stay unchanged.",
      )
    )
      return false;
    if (sourceDirty) {
      el("reply-text").value = "";
      el("reply-filename").value = "pasted-reply.txt";
      request = null;
    }
    if (actionDirty) {
      chosen = null;
      el("reply-action-form").hidden = true;
      el("reply-note").value = "";
    }
    sourceDirty = false;
    actionDirty = false;
    return true;
  }
  async function choose(record, index) {
    if (working || !state().canEdit) return;
    if (actionDirty && !window.confirm("Discard this unsaved follow-up note?"))
      return;
    const s = state(),
      v = generation,
      pick = ++selectionVersion;
    try {
      const board = await api.read(`/runs/${s.reference}/cases`);
      if (!valid(s, v) || pick !== selectionVersion || !api.isVisible()) return;
      chosen = {
        replyId: record.request_id,
        index,
        proposal: record.outcome.proposals[index],
      };
      boardGeneration = board.generation;
      actionDirty = false;
      el("reply-action-title").textContent =
        `Record follow-up · ${chosen.proposal.invoice_ref}`;
      el("reply-action-context").textContent =
        `Source: ${record.filename} · reply ${record.request_id}. This confirmation records your decision; it changes no financial balance and sends nothing.`;
      const history = board.history
        .filter((item) => item.evidence_id === chosen.proposal.evidence_id)
        .slice(-3);
      el("reply-existing-history").textContent = history.length
        ? "Latest follow-up:\n" +
          history
            .map(
              (item) =>
                `${{ checking: "Needs checking", waiting: "Waiting for hotel", resolved: "Resolved" }[item.status]} · ${item.actor}: ${item.note}`,
            )
            .join("\n")
        : "No earlier human follow-up recorded for this invoice.";
      el("reply-note").value = chosen.proposal.next_step.slice(0, 350);
      el("reply-status").value = chosen.proposal.suggested_status;
      el("reply-action-form").hidden = false;
      syncControls();
      el("reply-action-form").scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
      el("reply-actor").focus({ preventScroll: true });
    } catch (error) {
      if (valid(s, v)) {
        api.expired(error);
        message(`${error.message}. The proposal was not recorded.`, true);
      }
    }
  }
  function render(value) {
    const list = el("reply-list");
    list.replaceChildren();
    for (const record of [...value.replies].reverse()) {
      const article = node("article", null, "reply-record"),
        heading = node("div", null, "reply-record-heading");
      heading.append(
        node("h3", record.filename),
        node(
          "span",
          record.status === "finished"
            ? "Analysis saved"
            : record.status === "pending"
              ? "Analysis pending"
              : "Outcome unconfirmed",
          "state-chip",
        ),
      );
      article.append(heading);
      const original = node("details"),
        summary = node("summary", "Original hotel reply");
      original.append(
        summary,
        node("pre", record.text, "reply-original"),
        node("p", `Source text SHA-256: ${record.sha256}`, "source-hash"),
      );
      article.append(original);
      if (record.status !== "finished")
        article.append(
          node(
            "p",
            "This request is not restarted automatically. Refresh saved replies to inspect its outcome.",
            "helper",
          ),
        );
      else {
        const outcome = record.outcome;
        if (outcome.status !== "complete")
          article.append(
            node(
              "p",
              {
                rate_limited: "Wait five seconds between model requests.",
                allowance_exhausted:
                  "The daily model allowance is reached. Your original reply remains saved.",
                invalid_answer:
                  "The analysis could not be verified. No unchecked proposal is shown.",
                timeout:
                  "The analysis timed out. Your original reply remains saved.",
                provider_error:
                  "The model could not finish. Your original reply remains saved.",
                call_limit: "The analysis reached its request limit.",
              }[outcome.status] || "The analysis is unavailable.",
              "reply-unavailable",
            ),
          );
        for (const [index, proposal] of outcome.proposals.entries()) {
          const card = node("section", null, "reply-proposal");
          card.append(
            node("h4", proposal.invoice_ref),
            node(
              "span",
              "Hotel assertion · not independently verified",
              "claim-label",
            ),
            node("blockquote", proposal.quote),
            node("p", proposal.summary, "reply-evidence"),
            node("p", proposal.next_step),
          );
          const evidence = node(
            "button",
            "View invoice evidence",
            "quiet-link",
          );
          evidence.type = "button";
          evidence.addEventListener("click", () =>
            api.openEvidence(proposal.invoice_ref),
          );
          card.append(evidence);
          const confirm = node(
            "button",
            record.applied?.[String(index)]
              ? "Follow-up already recorded"
              : "Review proposed action",
            "btn primary reply-review",
          );
          confirm.type = "button";
          confirm.dataset.applied = String(
            Boolean(record.applied?.[String(index)]),
          );
          confirm.disabled =
            Boolean(record.applied?.[String(index)]) || !state().canEdit;
          confirm.addEventListener("click", () => choose(record, index));
          card.append(confirm);
          article.append(card);
        }
        article.append(
          node(
            "p",
            `${outcome.model_calls} model requests${outcome.reported_tokens == null ? "" : ` · ${outcome.reported_tokens} reported tokens`}`,
            "helper",
          ),
        );
      }
      list.append(article);
    }
    if (!value.replies.length)
      list.append(
        node(
          "p",
          "No hotel replies saved for this statement. Add a reply to start.",
          "empty-note",
        ),
      );
  }
  async function load() {
    const s = state(),
      v = generation;
    if (!s.connected) return;
    try {
      const value = await api.read(`/runs/${s.reference}/replies`);
      if (valid(s, v)) render(value);
    } catch (error) {
      if (valid(s, v)) {
        api.expired(error);
        message(`${error.message}. Saved replies could not load.`, true);
      }
    }
  }
  el("reply-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const s = state();
    if (working || !s.canEdit || !s.modelEnabled) return;
    const text = el("reply-text").value,
      filename = el("reply-filename").value.trim() || "pasted-reply.txt";
    if (Array.from(text).length < 12 || Array.from(text).length > 8000) {
      message("Enter 12 to 8,000 characters of reply text.", true);
      return;
    }
    if (!request || request.text !== text || request.filename !== filename)
      request = { request_id: crypto.randomUUID(), text, filename };
    const v = generation;
    working = true;
    api.setBusy(true);
    syncControls();
    message("Reading the original reply and checking its invoice evidence…");
    try {
      const value = await api.write(
        `/runs/${s.reference}/replies`,
        request,
        85000,
      );
      if (!valid(s, v)) return;
      sourceDirty = false;
      message(
        value.reply.status === "finished"
          ? "Analysis saved. Read the quoted source and review any proposal before recording it."
          : "The outcome is unconfirmed. Refresh saved replies; this request will not restart automatically.",
      );
      await load();
    } catch (error) {
      if (valid(s, v)) {
        api.expired(error);
        message(
          `${error.message}. Your entered text is retained. Refresh saved replies before retrying.`,
          true,
        );
      }
    } finally {
      if (v === generation) {
        working = false;
        api.setBusy(false);
        syncControls();
      }
    }
  });
  el("reply-action-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (working || !chosen || !state().canEdit) return;
    const s = state(),
      v = generation,
      target = chosen;
    const body = {
      proposal_index: target.index,
      expected_generation: boardGeneration,
      status: el("reply-status").value,
      actor: el("reply-actor").value.trim(),
      note: el("reply-note")
        .value.trim()
        .replace(/[\r\n\t]+/g, " "),
    };
    working = true;
    api.setBusy(true);
    syncControls();
    message("Recording your confirmed follow-up…");
    try {
      const updatedBoard = await api.write(
        `/runs/${s.reference}/replies/${encodeURIComponent(target.replyId)}/apply`,
        body,
        45000,
      );
      if (!valid(s, v)) return;
      actionDirty = false;
      chosen = null;
      el("reply-action-form").hidden = true;
      api.onRecorded(updatedBoard);
      message(
        "Follow-up recorded with its original hotel reply reference. Financial evidence is unchanged.",
      );
      await load();
    } catch (error) {
      if (valid(s, v)) {
        api.expired(error);
        message(
          `${error.message}. Your note is retained. Reopen the proposal to check the latest follow-up before retrying.`,
          true,
        );
      }
    } finally {
      if (v === generation) {
        working = false;
        api.setBusy(false);
        syncControls();
      }
    }
  });
  el("reply-file").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const v = generation;
    if (file.size > 32000 || !/\.(txt|csv)$/i.test(file.name)) {
      message("Choose a UTF-8 .txt or .csv reply of up to 32 KB.", true);
      return;
    }
    try {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      if (v !== generation) return;
      if (Array.from(text).length > 8000)
        throw new Error("Reply exceeds 8,000 characters");
      el("reply-text").value = text;
      el("reply-filename").value = file.name.slice(0, 120);
      sourceDirty = true;
      request = null;
      message(
        "Text loaded for your inspection. Press Analyse reply when ready; no model request has been made.",
      );
    } catch {
      if (v === generation)
        message(
          "The file could not be read as UTF-8 reply text within the displayed limits.",
          true,
        );
    }
  });
  el("use-reply-sample").addEventListener("click", () => {
    if (
      sourceDirty &&
      !window.confirm(
        "Replace the unsaved reply text with the fictional sample?",
      )
    )
      return;
    el("reply-text").value =
      "For INV-0002 we issued credit note CR-PMI-25 for EUR 25.00; please confirm it is recorded.";
    el("reply-filename").value = "mallorca-hotel-reply.txt";
    sourceDirty = true;
    request = null;
    message(
      "Fictional Mallorca reply loaded. Inspect it, then explicitly choose Analyse reply.",
    );
  });
  for (const id of ["reply-text", "reply-filename"])
    el(id).addEventListener("input", () => {
      sourceDirty = true;
      request = null;
    });
  for (const id of ["reply-actor", "reply-note", "reply-status"])
    el(id).addEventListener("input", () => {
      actionDirty = true;
    });
  el("refresh-replies").addEventListener("click", () => {
    if (working) return;
    if (
      actionDirty &&
      !window.confirm(
        "Discard the unsaved follow-up note and reload saved replies?",
      )
    )
      return;
    actionDirty = false;
    chosen = null;
    el("reply-action-form").hidden = true;
    load();
  });
  return {
    load,
    syncControls,
    allowLeave,
    reset() {
      selectionVersion++;
      generation++;
      working = false;
      sourceDirty = false;
      actionDirty = false;
      request = null;
      chosen = null;
      el("reply-text").value = "";
      el("reply-filename").value = "pasted-reply.txt";
      el("reply-note").value = "";
      el("reply-actor").value = "";
      el("reply-list").replaceChildren();
      el("reply-action-form").hidden = true;
      message("");
    },
  };
}

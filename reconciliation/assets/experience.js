function createExperience(api) {
  const el = (id) => document.getElementById(id);
  const scenarios = {
    credit: {
      invoice: "INV-0002",
      finding: "amount_mismatch",
      title: "Missing credit note",
      task: "The hotel claims EUR 239.00; finance records EUR 214.00. Check whether a credit explains the EUR 25.00 difference. A difference alone does not prove a credit exists.",
    },
    currency: {
      invoice: "INV-0003",
      finding: "currency_mismatch",
      title: "Currency mismatch",
      task: "The statement says GBP 221.00; finance records EUR 221.00. Check the agreed invoice currency before comparing balances. No exchange rate is applied.",
    },
    duplicate: {
      invoice: "INV-0004",
      finding: "ambiguous",
      title: "Duplicate invoice",
      task: "The hotel statement lists this reference twice. Inspect both source rows and confirm which entry should remain; matching amounts do not prove a duplicate payment.",
    },
  };
  let active = null;
  function selected() {
    return Object.hasOwn(scenarios, el("sample-scenario").value)
      ? el("sample-scenario").value
      : "credit";
  }
  function describe() {
    el("scenario-description").textContent = scenarios[selected()].task;
  }
  function current() {
    const state = api.getState();
    return (
      active &&
      active.reference === state.reference &&
      state.saved?.result.rows.some(
        (row) =>
          row.invoice_ref === active.invoice && row.status === active.finding,
      )
    );
  }
  function syncControls() {
    el("scenario-task").hidden = !current();
    for (const id of ["scenario-evidence", "scenario-ask", "scenario-followup"])
      el(id).disabled = !current() || !api.getState().canEdit;
  }
  function activate(key) {
    active = null;
    const value = scenarios[key],
      state = api.getState();
    if (
      value &&
      state.saved?.result.rows.some(
        (row) =>
          row.invoice_ref === value.invoice && row.status === value.finding,
      )
    ) {
      active = { ...value, reference: state.reference };
      el("scenario-title").textContent = `${value.title} · ${value.invoice}`;
      el("scenario-instruction").textContent = value.task;
    }
    syncControls();
    return Boolean(active);
  }
  function outcome(row, entry, questions = []) {
    const blocks = questions
      .filter(
        (record) =>
          record.status === "finished" && record.outcome?.status === "complete",
      )
      .flatMap((record) => record.outcome.blocks || []);
    const answer = blocks
      .filter(
        (block) =>
          block.invoice_ref === row.invoice_ref &&
          block.evidence_ids?.includes(row.id),
      )
      .at(-1);
    const status = entry?.status || "checking";
    const remaining =
      status === "resolved"
        ? "Recorded as resolved by a person. The original source finding remains; no balance or payment was changed."
        : status === "waiting"
          ? "We are waiting for the hotel. Check the response against the source records before closing this case."
          : "This difference still needs checking. Inspect the source records and record what should happen next.";
    return {
      assistant:
        answer?.text ||
        "No completed cited answer for this invoice is loaded. You can ask the assistant from its source evidence.",
      decision: entry
        ? `${entry.actor}: ${entry.note}`
        : "No human follow-up has been recorded.",
      remaining,
      status,
    };
  }
  el("sample-scenario").addEventListener("change", describe);
  el("scenario-evidence").addEventListener("click", async () => {
    if (!current() || !api.getState().canEdit) return;
    const ref = active.reference,
      invoice = active.invoice;
    try {
      await api.openEvidence(invoice);
    } catch (error) {
      if (api.getState().reference === ref) api.feedback(error.message);
    }
  });
  el("scenario-ask").addEventListener("click", () => {
    if (current() && api.getState().canEdit) api.ask(active.invoice);
  });
  el("scenario-followup").addEventListener("click", () => {
    if (current() && api.getState().canEdit) api.followup(active.invoice);
  });
  describe();
  return {
    selected,
    activate,
    outcome,
    syncControls,
    invoice: () => (current() ? active.invoice : ""),
    reset() {
      active = null;
      syncControls();
    },
  };
}

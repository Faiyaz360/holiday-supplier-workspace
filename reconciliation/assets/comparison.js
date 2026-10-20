function createComparison(api) {
  const el = (id) => document.getElementById(id);
  let generation = 0,
    loading = false;
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const validRef = (value) => /^[a-f0-9]{20}$/.test(value);
  const message = (text, error = false) => {
    el("comparison-feedback").textContent = text;
    el("comparison-feedback").dataset.tone = error ? "error" : "info";
  };
  const finding = (value) =>
    ({
      matched: "Matched",
      amount_mismatch: "Amount difference",
      currency_mismatch: "Currency difference",
      ambiguous: "Duplicate / ambiguous",
      statement_only: "Statement only",
      ledger_only: "Ledger only",
    })[value] || "Not present";
  function render(value) {
    el("comparison-summary").textContent =
      `Previous ${value.previous.statement_id || "statement"} · current ${value.current.statement_id || "statement"}. ${value.counts.changed} changed, ${value.counts.new} new, ${value.counts.removed} removed, ${value.counts.unchanged} unchanged; ${value.counts.still_needs_review} still needs review.`;
    const list = el("comparison-list");
    list.replaceChildren();
    for (const row of value.rows || []) {
      if (row.change === "unchanged" && !row.still_needs_review) continue;
      const card = node(
        "article",
        null,
        `comparison-card comparison-${row.change}`,
      );
      card.append(
        node(
          "h3",
          `${row.invoice_ref} · ${{ new: "New on statement", removed: "Removed from statement", changed: "Changed", unchanged: "Unchanged" }[row.change]}`,
        ),
      );
      card.append(
        node(
          "p",
          `${finding(row.before_status)} → ${finding(row.after_status)}`,
        ),
      );
      const lines = node("div");
      for (const [label, cells] of [
        ["Before", row.before],
        ["After", row.after],
      ]) {
        const section = node("section");
        section.append(node("h4", label));
        section.append(
          node(
            "p",
            cells.length
              ? cells
                  .map(
                    (c) =>
                      `${c.source === "ledger" ? "Finance ledger" : c.source === "statement" ? "Hotel statement" : "Unknown source"}: ${c.currency || "Currency unknown"} ${c.amount ?? "Amount unknown"}${c.booking_ref ? ` · booking ${c.booking_ref}` : ""}`,
                  )
                  .join("; ")
              : "No statement lines",
          ),
        );
        lines.append(section);
      }
      card.append(lines);
      if (row.still_needs_review)
        card.append(
          node(
            "p",
            "Still needs review in both statements; this comparison does not claim it was resolved.",
            "comparison-review",
          ),
        );
      if (row.change === "removed")
        card.append(
          node(
            "p",
            "Removed from the current statement; this does not prove it was paid.",
          ),
        );
      list.append(card);
    }
    if (!value.rows?.length)
      list.append(node("p", "No invoice changes found."));
  }
  async function compare(event) {
    event.preventDefault();
    if (loading) return;
    const previous = el("comparison-previous").value.trim(),
      state = api.getState(),
      version = ++generation;
    if (!validRef(previous)) {
      message("Enter a 20-character statement reference.", true);
      return;
    }
    if (!state.connected) {
      message("Connect to a saved statement first.", true);
      return;
    }
    loading = true;
    syncControls();
    message("Comparing saved statements…");
    try {
      const value = await api.read(
        `/runs/${state.reference}/comparison?previous=${encodeURIComponent(previous)}`,
      );
      const now = api.getState();
      if (
        version !== generation ||
        now.reference !== state.reference ||
        now.epoch !== state.epoch
      )
        return;
      render(value);
      message("Comparison loaded. No statement records were changed.");
    } catch (error) {
      if (version === generation) {
        api.expired(error);
        message(`${error.message}. Comparison could not load.`, true);
      }
    } finally {
      if (version === generation) loading = false;
      syncControls();
    }
  }
  function syncControls() {
    const state = api.getState();
    el("compare-statements").disabled = loading || !state.connected;
    el("comparison-previous").disabled = loading;
    const example = el("load-comparison-example");
    if (example) example.disabled = loading;
  }
  async function loadExample() {
    if (loading) return;
    const state = api.getState(),
      version = ++generation;
    if (!state.connected) {
      message("Connect to a saved statement first.", true);
      return;
    }
    loading = true;
    syncControls();
    message("Loading fictional comparison example…");
    try {
      const value = await api.read("/workspace/comparison-example"),
        now = api.getState();
      if (
        version !== generation ||
        now.reference !== state.reference ||
        now.epoch !== state.epoch
      )
        return;
      render(value);
      message("Fictional example; no saved records changed.");
    } catch (error) {
      const now = api.getState();
      if (
        version === generation &&
        now.reference === state.reference &&
        now.epoch === state.epoch
      ) {
        api.expired(error);
        message(`${error.message}. Example could not load.`, true);
      }
    } finally {
      if (version === generation) loading = false;
      syncControls();
    }
  }
  el("comparison-form").addEventListener("submit", compare);
  const exampleButton = el("load-comparison-example");
  if (exampleButton) exampleButton.addEventListener("click", loadExample);
  function reset() {
    generation++;
    loading = false;
    el("comparison-previous").value = "";
    el("comparison-summary").textContent =
      "Compare two saved statements to see what changed.";
    el("comparison-list").replaceChildren();
    message("");
    syncControls();
  }
  return {
    renderExample: render,
    showResult: render,
    loadExample,
    setPrevious(reference) {
      el("comparison-previous").value = reference || "";
    },
    reset,
    syncControls,
  };
}

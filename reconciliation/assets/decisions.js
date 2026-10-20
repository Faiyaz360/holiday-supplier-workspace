function createDecisions(api) {
  const el = (id) => document.getElementById(id);
  const names = {
    jev: "Jev 1.14",
    d1_ultra: "d1-ultra",
    gemini: "Gemini · language model",
    rules: "Transparent rules",
  };
  const labels = {
    credit: "Credit issued",
    payment: "Payment made",
    currency: "Currency disputed",
    duplicate: "Duplicate reported",
    documents: "Documents requested",
    clarification: "Clarification needed",
    next_step: "Next step",
    priority: "Review priority",
    reference_present: "Invoice reference visible",
    currency_matches: "Credit currency is EUR",
    legible: "Reference and currency legible",
    reference_location: "Experimental reference region",
  };
  const routes = {
    investigate_evidence: "Investigate evidence",
    request_document: "Prepare requested document",
    clarify_reference: "Clarify invoice reference",
    no_new_followup: "No new follow-up",
  };
  const priorities = ["Routine", "Normal", "Time-sensitive", "Urgent"];
  let data = null,
    working = false,
    generation = 0,
    pending = null;
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const usd = (value) =>
    typeof value === "number" && Number.isFinite(value)
      ? "$" + value.toFixed(value < 0.01 ? 6 : 4)
      : "Unknown";
  const time = (value) =>
    typeof value === "number" && Number.isFinite(value)
      ? value >= 1000
        ? (value / 1000).toFixed(2) + " s"
        : value.toFixed(0) + " ms"
      : "Not measured";
  const chosen = () =>
    data?.cases.find((c) => c.id === el("decision-case").value) ||
    data?.cases[0];
  const comparisons = () => [
    ...(data?.recordings || []),
    ...(data?.comparisons || []),
  ];
  const latest = () =>
    comparisons()
      .filter((r) => r.case_id === chosen()?.id)
      .at(-1);
  function syncControls() {
    const s = api.getState();
    el("decision-availability").textContent = s.enabled
      ? "Fresh comparisons use model credits."
      : "This session shows recorded results; fresh comparisons are unavailable.";
    el("decision-case").disabled = working || !s.connected;
    el("decision-refresh").disabled = working || !s.connected;
    el("decision-run").disabled =
      working ||
      !s.canEdit ||
      !s.enabled ||
      Boolean(data?.budget.stopped) ||
      Boolean(latest() && ["pending", "uncertain"].includes(latest().status));
  }
  function scalar(key, item) {
    if (item && typeof item === "object") {
      if (item.type === "noul") return item.noul >= 0.5;
      if (item.type === "choice") return item.choice;
      if (item.type === "score")
        return Number(
          Object.keys(item.probabilities).reduce((a, b) =>
            item.probabilities[a] >= item.probabilities[b] ? a : b,
          ),
        );
    }
    return item;
  }
  function pretty(key, value) {
    if (value?.type === "box")
      return value.confidence < 0.8
        ? "Region withheld · " +
            Math.round(value.confidence * 100) +
            "% estimated confidence; 80% required"
        : "Predicted region · " +
            Math.round(value.confidence * 100) +
            "% estimated confidence; see document";
    return typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : key === "next_step"
        ? routes[value] || value
        : key === "priority"
          ? priorities[value] || String(value)
          : String(value ?? "Unavailable");
  }
  function threshold() {
    const value = Number(el("decision-threshold").value) || 0.8;
    el("decision-threshold-value").textContent = Math.round(value * 100) + "%";
    const list = el("decision-routes");
    list.replaceChildren();
    const record = latest();
    if (!record) return;
    const context = record.task?.state || {};
    for (const name of ["jev", "d1_ultra"]) {
      const r = record.results[name],
        answer = r?.answers?.next_step;
      if (r?.status !== "complete" || !answer?.probabilities) continue;
      const missing =
        !context.message_references?.length ||
        context.unknown_references?.length;
      const probability = answer.probabilities[answer.choice];
      const title = missing
        ? "Clarify invoice reference"
        : probability < value
          ? "Human review"
          : routes[answer.choice];
      const card = node("div", null, "decision-route");
      card.append(
        node("strong", names[name]),
        node("span", title),
        node(
          "small",
          missing
            ? "Application override: missing or unknown exact reference"
            : Math.round(probability * 100) +
                "% estimated next-step probability",
        ),
      );
      list.append(card);
    }
  }
  function renderSummary() {
    const list = el("decision-summary");
    list.replaceChildren();
    const modelKeys = ["jev", "d1_ultra", "gemini"];
    const matched = data?.matched_summary || {};
    const chartGroup = node("div", null, "decision-chart-grid");
    const measures = [
      {
        title: "Response time",
        note: "Lower is faster · median seconds per completed response",
        value: (s) => (s?.completed ? s.median_ms : null),
        format: (v) => (v / 1000).toFixed(2) + " s",
      },
      {
        title: "Model cost",
        note: "Lower costs less · USD per 1,000 equivalent completed cases",
        value: (s) => (s?.cost_complete ? s.cost_per_1000_successes_usd : null),
        format: usd,
      },
      {
        title: "Answer agreement",
        note: "Higher agrees more · author-written draft answers",
        value: (s) => (s?.total ? s.correct / s.total : null),
        format: (v, s) => `${s.correct}/${s.total} · ${Math.round(v * 100)}%`,
      },
    ];
    for (const measure of measures) {
      const chart = node("section", null, "decision-chart");
      chart.append(
        node("h4", measure.title),
        node("p", measure.note, "decision-chart-note"),
      );
      const values = modelKeys.map((key) => measure.value(matched[key]));
      const scale =
        measure.title === "Answer agreement"
          ? 1
          : Math.max(
              0,
              ...values.filter(
                (v) => typeof v === "number" && Number.isFinite(v),
              ),
            ) || 1;
      modelKeys.forEach((key, index) => {
        const stats = matched[key],
          value = values[index];
        const known =
          typeof value === "number" && Number.isFinite(value) && value >= 0;
        const row = node("div", null, "decision-compare-row");
        row.dataset.model = key;
        const label = node(
          "span",
          key === "gemini" ? "Gemini" : names[key],
          "decision-model-name",
        );
        const valueLabel = known
          ? measure.format(value, stats)
          : !stats
            ? "Not measured"
            : measure.title === "Model cost"
              ? "Unknown · usage incomplete"
              : "Not measured";
        const track = node("div", null, "decision-track");
        if (known) {
          const bar = node("div", null, "decision-bar");
          bar.style.width = Math.min(value / scale, 1) * 100 + "%";
          track.append(bar);
        }
        track.setAttribute("role", "img");
        track.setAttribute("aria-label", `${names[key]}: ${valueLabel}`);
        row.append(
          label,
          track,
          node("strong", valueLabel, "decision-compare-value"),
        );
        chart.append(row);
      });
      chartGroup.append(chart);
    }
    list.append(chartGroup);
    const detail = node("details", null, "decision-measurement-details");
    detail.append(node("summary", "Reliability, costs and missed claims"));
    detail.append(
      node(
        "p",
        "The bars use the same completed text cases for all three models. Bars begin at zero; each measure uses its own scale. Specialist charges are reported; Gemini costs are estimates. Unknown usage stays unknown.",
        "helper",
      ),
    );
    const detailCards = node("div", null, "decision-detail-grid");
    detail.append(detailCards);
    list.append(detail);

    for (const name of ["jev", "d1_ultra", "gemini", "rules"]) {
      const s = (data?.matched_summary || data?.summary)?.[name],
        all = data?.summary[name],
        card = node("article", null, "decision-metric-card");
      card.append(
        node(
          "span",
          name === "rules"
            ? "Code baseline"
            : name === "gemini"
              ? "Language model"
              : "Decision model",
          "eyebrow",
        ),
        node("h4", names[name]),
      );
      const metrics = node("dl");
      for (const [label, value] of [
        [
          "Draft-label agreement",
          s?.total
            ? `${s.correct}/${s.total} · ${Math.round((s.correct / s.total) * 100)}%`
            : "Not run",
        ],
        [
          "Median response",
          name === "rules"
            ? "Local rules; not timed"
            : s?.completed
              ? time(s.median_ms)
              : "Not measured",
        ],
        [
          "Model cost",
          s
            ? usd(s.known_cost_usd) +
              (s.cost_complete ? "" : " + unknown charges")
            : "Not measured",
        ],
      ]) {
        metrics.append(node("dt", label), node("dd", value));
      }
      card.append(metrics);
      if (s) {
        card.append(
          node(
            "p",
            `${s.completed}/${s.attempts} requests completed · ${s.complete_cases}/${s.scored_cases} fully correct scored examples`,
            "helper",
          ),
        );
        card.append(
          node(
            "p",
            s.cost_per_1000_successes_usd == null
              ? "Per 1,000: unavailable (incomplete usage)"
              : `Per 1,000 completed cases: ${usd(s.cost_per_1000_successes_usd)} · projection`,
            "helper",
          ),
        );
        if (s.estimated_costs)
          card.append(
            node(
              "p",
              "Includes " + s.estimated_costs + " token-price estimate(s).",
              "helper",
            ),
          );
        const details = node("details"),
          summary = node("summary", "Missed claims and false flags");
        details.append(summary);
        for (const [key, counts] of Object.entries(s.claims || {}))
          details.append(
            node(
              "p",
              `${labels[key]}: ${counts.tp} caught · ${counts.fn} missed · ${counts.fp} false flags`,
              "helper",
            ),
          );
        card.append(details);
      }
      if (all)
        card.append(
          node(
            "p",
            `All attempts: ${all.completed}/${all.attempts} completed · total ${usd(all.known_cost_usd)}${all.cost_complete ? "" : " + unknown charges"}`,
            "helper",
          ),
        );
      detailCards.append(card);
    }
    const n = comparisons().filter((r) => r.task?.mode !== "visual").length;
    const matchedCount = data?.matched_cases?.length ?? 0;
    el("decision-provenance").textContent = matchedCount
      ? `Compared on ${matchedCount} shared fictional hotel cases. Agreement uses draft reference answers, not independently verified accuracy.`
      : n
        ? "No shared completed cases yet. Open the measurement details to inspect saved attempts."
        : "No model results yet. Run the same fictional task to measure the difference.";
    detail.append(
      node(
        "p",
        `${n} total text attempts are retained, including failures. Recorded fictional examples are separate from this statement. Completed-response medians do not describe failure rates. Different completed-case sets are not a fair speed ranking.`,
        "helper",
      ),
    );
  }
  function renderCase() {
    const selected = chosen(),
      record = latest();
    el("decision-source").textContent =
      selected?.message || "Choose a fictional hotel case.";
    el("decision-mode").textContent =
      selected?.mode === "visual"
        ? "Visual reading experiment · separate from the text comparison"
        : "Six claim checks, one next-step choice and one review-priority score.";
    el("decision-box").hidden = true;
    el("decision-image").hidden =
      selected?.mode !== "visual" || !data?.fixture_image;
    if (data?.fixture_image?.startsWith("data:image/png;base64,"))
      el("decision-image").src = data.fixture_image;
    const list = el("decision-results");
    list.replaceChildren();
    el("decision-whatif").hidden = !record || selected?.mode === "visual";
    el("decision-recorded").hidden = !record;
    el("decision-recorded").textContent = record
      ? `${record.status === "pending" ? "In progress" : "Recorded"} · ${new Date(record.started_at * 1000).toLocaleString()}`
      : "";
    if (!record) {
      list.append(
        node(
          "p",
          "No saved comparison for this case. A fresh comparison uses model credits; reload and replay are free.",
          "empty-note",
        ),
      );
      syncControls();
      return;
    }
    const expected = data.labels?.[record.case_id] || {};
    for (const name of ["jev", "d1_ultra", "gemini", "rules"]) {
      if (selected?.mode === "visual" && ["gemini", "rules"].includes(name))
        continue;
      const r = record.results[name],
        card = node("article", null, "decision-result");
      card.append(node("h4", names[name]));
      if (!r) {
        card.append(
          node(
            "p",
            "Not completed. Reload saved results; no request is retried automatically.",
            "helper",
          ),
        );
        list.append(card);
        continue;
      }
      card.append(
        node(
          "span",
          r.status === "complete" ? "Completed" : r.status.replaceAll("_", " "),
          "decision-tag",
        ),
      );
      card.append(
        node(
          "p",
          `${time(r.elapsed_ms)} · ${usd(r.cost?.usd)} · ${r.cost?.kind || "unknown"} cost`,
          "decision-result-meta",
        ),
      );
      if (r.status === "complete") {
        const location = r.answers?.reference_location;
        if (
          name === "jev" &&
          location?.type === "box" &&
          location.confidence >= 0.8 &&
          r.answers.reference_present?.noul >= 0.8
        ) {
          const b = location.box,
            overlay = el("decision-box");
          overlay.style.left = b.left * 100 + "%";
          overlay.style.top = b.top * 100 + "%";
          overlay.style.width = (b.right - b.left) * 100 + "%";
          overlay.style.height = (b.bottom - b.top) * 100 + "%";
          overlay.hidden = false;
        }
        const entries = node("ul", null, "decision-answer-list");
        for (const [key, item] of Object.entries(r.answers || {})) {
          const value = scalar(key, item),
            row = node("li"),
            matched = Object.hasOwn(expected, key)
              ? value === expected[key]
              : null;
          row.append(
            node("strong", labels[key] || key),
            node("span", pretty(key, value)),
          );
          if (item && typeof item === "object" && item.type === "noul") {
            const meter = node("meter");
            meter.min = 0;
            meter.max = 1;
            meter.value = item.noul;
            meter.setAttribute(
              "aria-label",
              `${labels[key]}: ${Math.round(item.noul * 100)} percent estimated yes probability`,
            );
            row.append(
              meter,
              node("small", Math.round(item.noul * 100) + "% estimated yes"),
            );
          }
          if (matched !== null) {
            row.dataset.match = String(matched);
            row.append(
              node(
                "small",
                matched
                  ? "Agrees with draft label"
                  : "Draft label: " + pretty(key, expected[key]),
              ),
            );
          }
          entries.append(row);
        }
        card.append(entries);
        if (r.limitations) card.append(node("p", r.limitations, "helper"));
      } else
        card.append(
          node(
            "p",
            "No unchecked answer is used. The source records are unchanged.",
            "helper",
          ),
        );
      const detail = node("details");
      detail.append(
        node("summary", "Model, usage and cost source"),
        node(
          "pre",
          JSON.stringify(
            {
              requested_model: r.requested_model,
              returned_model: r.returned_model,
              provider: r.provider,
              usage: r.usage,
              cost: r.cost,
              question_version: record.task?.question_version,
            },
            null,
            2,
          ),
          "decision-technical",
        ),
      );
      card.append(detail);
      list.append(card);
    }
    threshold();
    renderQueue();
    syncControls();
  }
  function renderQueue() {
    const list = el("decision-queue");
    list.replaceChildren();
    for (const name of ["jev", "d1_ultra"]) {
      const seen = new Set(),
        items = [];
      for (const r of comparisons().slice().reverse()) {
        if (seen.has(r.case_id)) continue;
        const outcome = r.results[name],
          priority = outcome?.answers?.priority;
        if (
          r.task?.mode !== "text" ||
          outcome?.status !== "complete" ||
          !priority?.probabilities
        )
          continue;
        seen.add(r.case_id);
        items.push({
          title: data.cases.find((c) => c.id === r.case_id)?.title || r.case_id,
          score: priority.probability_weighted_score ?? priority.score,
        });
      }
      if (!items.length) continue;
      const section = node("section", null, "decision-route");
      section.append(node("strong", names[name] + " · review queue"));
      const order = node("ol");
      for (const item of items.sort((a, b) => b.score - a.score).slice(0, 5))
        order.append(
          node("li", item.title + " · " + item.score.toFixed(2) + "/3"),
        );
      section.append(order);
      list.append(section);
    }
  }
  async function load() {
    const state = api.getState(),
      version = ++generation;
    if (!state.connected) return;
    try {
      const value = await api.read(`/runs/${state.reference}/decisions`);
      const current = api.getState();
      if (
        version !== generation ||
        current.reference !== state.reference ||
        current.epoch !== state.epoch
      )
        return;
      const previous = el("decision-case").value;
      data = value;
      el("decision-case").replaceChildren(
        ...data.cases.map((c) => {
          const option = node(
            "option",
            c.title + (c.mode === "visual" ? " · visual experiment" : ""),
          );
          option.value = c.id;
          return option;
        }),
      );
      el("decision-case").value = data.cases.some((c) => c.id === previous)
        ? previous
        : data.cases[0]?.id || "";
      el("decision-budget").textContent =
        `Initial test allowance: ${usd(data.budget.limit_usd)} total. ${usd(data.budget.known_spend_usd)} confirmed usage. ${data.budget.stopped ? "Fresh requests paused; saved results remain available." : "No automatic top-ups."}`;
      renderSummary();
      renderCase();
    } catch (error) {
      api.expired?.(error);
      el("decision-feedback").textContent = error.message;
    }
  }
  async function run() {
    const state = api.getState(),
      selected = chosen();
    if (
      working ||
      !state.canEdit ||
      !state.enabled ||
      !selected ||
      data?.budget.stopped ||
      (api.allowStart && !api.allowStart())
    )
      return;
    working = true;
    pending = pending || {
      request_id: crypto.randomUUID(),
      case_id: selected.id,
      mode: selected.mode,
    };
    api.setBusy(true);
    syncControls();
    el("decision-feedback").textContent =
      "Comparing the same task. Results and any failures will be saved; please wait.";
    try {
      await api.write(`/runs/${state.reference}/decisions`, pending);
      if (
        api.getState().reference !== state.reference ||
        api.getState().epoch !== state.epoch
      )
        return;
      pending = null;
      await load();
      el("decision-feedback").textContent =
        "Saved comparison loaded. Replaying these results and changing the threshold make no model calls.";
    } catch (error) {
      api.expired?.(error);
      el("decision-feedback").textContent =
        "Outcome unconfirmed. Reload saved results before retrying. " +
        error.message;
    } finally {
      working = false;
      api.setBusy(false);
      syncControls();
    }
  }
  el("decision-run").addEventListener("click", run);
  el("decision-refresh").addEventListener("click", load);
  el("decision-case").addEventListener("change", () => {
    pending = null;
    renderCase();
  });
  el("decision-threshold").addEventListener("input", threshold);
  return {
    load,
    syncControls,
    allowLeave() {
      return !working;
    },
    reset() {
      generation++;
      data = null;
      pending = null;
      working = false;
      el("decision-results").replaceChildren();
      el("decision-summary").replaceChildren();
      el("decision-whatif").hidden = true;
      el("decision-image").hidden = true;
    },
  };
}

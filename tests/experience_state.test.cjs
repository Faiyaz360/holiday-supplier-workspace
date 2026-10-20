const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
function fixture() {
  const nodes = new Map();
  class Element extends EventTarget {
    constructor() {
      super();
      this.value = "credit";
      this.hidden = false;
      this.disabled = false;
      this.textContent = "";
    }
    focus() {}
    scrollIntoView() {}
  }
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new Element());
      return nodes.get(id);
    },
  };
  const state = {
    reference: "a".repeat(20),
    saved: {
      result: {
        rows: [
          { id: "R-0002", invoice_ref: "INV-0002", status: "amount_mismatch" },
          {
            id: "R-0003",
            invoice_ref: "INV-0003",
            status: "currency_mismatch",
          },
          { id: "R-0004", invoice_ref: "INV-0004", status: "ambiguous" },
        ],
      },
    },
    canEdit: true,
  };
  const calls = [];
  const context = vm.createContext({ document });
  vm.runInContext(
    readFileSync("reconciliation/assets/experience.js", "utf8"),
    context,
  );
  const ui = context.createExperience({
    getState: () => state,
    openEvidence: async (ref) => calls.push(ref),
    ask: (ref) => calls.push("ask:" + ref),
    followup: (ref) => calls.push("followup:" + ref),
    feedback: (msg) => calls.push(msg),
  });
  return { ui, node: (id) => document.getElementById(id), state, calls };
}
const row = {
  id: "R-0002",
  invoice_ref: "INV-0002",
  status: "amount_mismatch",
};
test("three scenarios focus only their own saved source finding", async () => {
  const f = fixture();
  for (const [key, ref] of [
    ["credit", "INV-0002"],
    ["currency", "INV-0003"],
    ["duplicate", "INV-0004"],
  ]) {
    assert.equal(f.ui.activate(key), true);
    f.node("scenario-evidence").dispatchEvent(new Event("click"));
    await new Promise(setImmediate);
    assert.equal(f.calls.at(-1), ref);
    assert.match(f.node("scenario-title").textContent, new RegExp(ref));
  }
  assert.equal(f.ui.activate("unknown"), false);
});
test("scenario controls cannot act after the statement changes or during busy work", () => {
  const f = fixture();
  f.ui.activate("credit");
  f.state.reference = "b".repeat(20);
  f.node("scenario-ask").dispatchEvent(new Event("click"));
  assert.equal(f.calls.length, 0);
  f.ui.syncControls();
  assert.equal(f.node("scenario-task").hidden, true);
  f.state.reference = "a".repeat(20);
  f.ui.activate("credit");
  f.state.canEdit = false;
  f.node("scenario-ask").dispatchEvent(new Event("click"));
  assert.equal(f.calls.length, 0);
});
test("scenario must match both invoice and finding; custom uploads cannot fabricate the story", () => {
  const f = fixture();
  f.state.saved.result.rows[0].status = "matched";
  assert.equal(f.ui.activate("credit"), false);
  assert.equal(f.node("scenario-task").hidden, true);
});
test("a resolved follow-up never claims the original financial difference was fixed", () => {
  const f = fixture();
  const result = f.ui.outcome(
    row,
    {
      status: "resolved",
      note: "Hotel confirmed the credit.",
      actor: "Reviewer",
    },
    [],
  );
  assert.match(result.remaining, /original source finding remains/i);
  assert.match(result.decision, /Hotel confirmed the credit/);
  assert.match(result.assistant, /No completed cited answer/);
});
test("only a completed answer citing this exact invoice evidence is included", () => {
  const f = fixture();
  const records = [
    {
      status: "finished",
      outcome: {
        status: "complete",
        blocks: [
          {
            invoice_ref: "INV-0002",
            evidence_ids: ["R-0003"],
            text: "Wrong evidence",
          },
        ],
      },
    },
    {
      status: "finished",
      outcome: {
        status: "complete",
        blocks: [
          {
            invoice_ref: "INV-0002",
            evidence_ids: ["R-0002"],
            text: "Check the credit note.",
          },
        ],
      },
    },
  ];
  assert.equal(
    f.ui.outcome(row, null, records).assistant,
    "Check the credit note.",
  );
  records[1].outcome.status = "invalid_answer";
  assert.match(
    f.ui.outcome(row, null, records).assistant,
    /No completed cited answer/,
  );
});
test("waiting and reopened conclusions state the current saved decision", () => {
  const f = fixture();
  assert.match(
    f.ui.outcome(
      row,
      { status: "waiting", actor: "A", note: "Requested credit." },
      [],
    ).remaining,
    /waiting for the hotel/i,
  );
  assert.match(
    f.ui.outcome(row, { status: "checking", actor: "A", note: "Reopened." }, [])
      .remaining,
    /still needs checking/i,
  );
});

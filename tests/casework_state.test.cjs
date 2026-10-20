const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");

test("cancelled workspace action performs no mutation and never clears the note", async () => {
  const source = readFileSync("reconciliation/assets/hosted.js", "utf8");
  const body = source.slice(
    source.indexOf("  async function operate("),
    source.indexOf("  async function connected("),
  );
  let calls = 0,
    clears = 0,
    controls = 0;
  const context = vm.createContext({
    casework: { allowLeave: () => false },
    epoch: 1,
    busy: false,
    controls: () => controls++,
    capabilities: {},
    saved: {},
    walkthrough: { offer() {} },
    clearView: () => clears++,
  });
  vm.runInContext(body, context);
  await context.operate(async () => {
    calls++;
  });
  assert.equal(calls, 0, "The cancelled action must not run its request");
  assert.equal(clears, 0);
  assert.equal(controls, 0);
});

function fixture() {
  const nodes = new Map(),
    events = [],
    writes = [];
  let confirms = 0,
    finish;
  class Element extends EventTarget {
    constructor() {
      super();
      this.children = [];
      this.style = {};
      this.dataset = {};
      this.value = "";
      this.disabled = false;
      this.hidden = false;
    }
    append(...items) {
      this.children.push(...items);
    }
    replaceChildren(...items) {
      this.children = items;
    }
    setAttribute() {}
    scrollIntoView() {
      this.scrollCount = (this.scrollCount || 0) + 1;
    }
    focus() {
      this.focusCount = (this.focusCount || 0) + 1;
    }
    querySelectorAll() {
      return this.children.filter((item) =>
        (item.className || "").split(" ").includes("case-card"),
      );
    }
  }
  const document = {
    dispatchEvent(event) {
      events.push(event);
    },
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new Element());
      return nodes.get(id);
    },
    createElement() {
      return new Element();
    },
  };
  const board = {
    generation: 0,
    history: [],
    cases: {},
    can_save: true,
    rows: [
      {
        id: "R-0002",
        invoice_ref: "INV-0002",
        status: "amount_mismatch",
        statement_lines: [{ currency: "EUR", claimed_amount: "239.00" }],
        ledger_lines: [{ currency: "EUR", outstanding_amount: "214.00" }],
      },
    ],
  };
  const state = {
    connected: true,
    reference: "a".repeat(20),
    epoch: 1,
    canEdit: true,
    saved: { result: { rows: board.rows } },
  };
  class CustomEvent extends Event {
    constructor(type, options) {
      super(type);
      this.detail = options.detail;
    }
  }
  const context = vm.createContext({
    CustomEvent,
    document,
    window: {
      confirm() {
        confirms++;
        return false;
      },
    },
    Intl,
    Date,
    crypto: require("node:crypto").webcrypto,
  });
  vm.runInContext(
    readFileSync("reconciliation/assets/casework.js", "utf8"),
    context,
  );
  const ui = context.createCasework({
    getState: () => state,
    preferredInvoice: () => state.preferredInvoice || "",
    read: async () => board,
    write: (path, body) => {
      writes.push(body);
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
    expired() {},
    feedback() {},
    selectActivity() {},
    openEvidence() {},
  });
  document.getElementById("case-filter").value = "all";
  return {
    ui,
    events,
    writes,
    node: (id) => document.getElementById(id),
    confirms: () => confirms,
    finish: () => finish(board),
    state,
  };
}

test("case save locks editing and navigation until it has an outcome", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  f.node("case-actor").value = "Verifier";
  f.node("case-note").value = "Waiting for the hotel";
  f.node("case-status").value = "waiting";
  f.node("case-note").dispatchEvent(new Event("input"));
  f.node("case-form").dispatchEvent(new Event("submit", { cancelable: true }));
  for (const id of ["case-actor", "case-note", "case-status"])
    assert.equal(
      f.node(id).disabled,
      true,
      id + " must not accept edits during save",
    );
  assert.equal(f.ui.allowLeave(), false);
  assert.equal(f.confirms(), 0, "Saving does not offer a discard dialog");
  assert.equal(f.node("case-list").children[0].disabled, true);
  f.finish();
  await new Promise(setImmediate);
  assert.equal(f.node("case-note").disabled, false);
  assert.equal(f.node("case-note").value, "");
  assert.equal(
    f.events.filter((e) => e.type === "workspace-case-saved").length,
    1,
  );
  const saved = f.events.find((e) => e.type === "workspace-case-saved").detail;
  assert.equal(saved.invoiceRef, "INV-0002");
  assert.equal(saved.status, "waiting");
});

test("declining to leave retains note and selected case", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  f.node("case-note").value = "Unsaved evidence";
  f.node("case-note").dispatchEvent(new Event("input"));
  assert.equal(f.ui.showStatement(), false);
  assert.equal(f.node("case-note").value, "Unsaved evidence");
  assert.equal(f.node("followup-view").hidden, false);
});

test("opening follow-up never reports an update as saved", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  assert.equal(f.events.length, 1);
  assert.equal(f.events[0].type, "workspace-followup-opened");
});

test("changing the case filter cannot discard an unsaved note", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  f.node("case-note").value = "Keep this draft";
  f.node("case-note").dispatchEvent(new Event("input"));
  f.node("case-filter").value = "resolved";
  f.node("case-filter").dispatchEvent(new Event("change"));
  assert.equal(f.node("case-filter").value, "all");
  assert.equal(f.node("case-note").value, "Keep this draft");
});
test("a filter with no matching case clears the old detail", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  f.node("case-filter").value = "resolved";
  f.node("case-filter").dispatchEvent(new Event("change"));
  assert.equal(f.node("case-detail").hidden, true);
});
test("multiline follow-up notes use the printable saved-note contract", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  f.node("case-actor").value = "Verifier";
  f.node("case-note").value =
    "Checked credit.\nRequested evidence.\tAwaiting response.";
  f.node("case-status").value = "waiting";
  f.node("case-form").dispatchEvent(new Event("submit", { cancelable: true }));
  assert.equal(
    f.writes[0].note,
    "Checked credit. Requested evidence. Awaiting response.",
  );
  f.finish();
  await new Promise(setImmediate);
});
test("reset clears the new conclusion text as well as the case history", () => {
  const f = fixture();
  f.node("case-outcome").append({ textContent: "Saved private conclusion" });
  f.node("case-outcome-status").textContent = "Waiting for hotel";
  f.ui.reset();
  assert.equal(f.node("case-outcome").children.length, 0);
  assert.equal(f.node("case-outcome-status").textContent, "");
});

test("normal Follow-up navigation selects the scenario invoice instead of a previous case", async () => {
  const f = fixture();
  f.state.saved.result.rows.push({
    id: "R-0003",
    invoice_ref: "INV-0003",
    status: "currency_mismatch",
    statement_lines: [],
    ledger_lines: [],
  });
  f.state.preferredInvoice = "INV-0003";
  f.node("view-followup").dispatchEvent(new Event("click"));
  await new Promise(setImmediate);
  assert.equal(f.node("case-title").textContent, "INV-0003");
});

test("page title follows the accepted view and stays on unsaved work when leaving is declined", async () => {
  const f = fixture();
  await f.ui.showFollowup();
  assert.equal(f.node("workspace-title").textContent, "Invoice follow-up");
  assert.equal(f.node("workspace-title").scrollCount, 1);
  assert.equal(f.node("workspace-title").focusCount, 1);
  f.node("case-note").value = "Keep my note";
  f.node("case-note").dispatchEvent(new Event("input"));
  assert.equal(f.ui.showStatement(), false);
  assert.equal(f.node("workspace-title").textContent, "Invoice follow-up");
});

test("reset restores the statement title with its visible view", () => {
  const f = fixture();
  f.ui.showView("responses");
  f.ui.reset();
  assert.equal(f.node("workspace-title").textContent, "Hotel statement review");
  assert.equal(f.node("statement-view").hidden, false);
  assert.equal(f.node("responses-view").hidden, true);
});

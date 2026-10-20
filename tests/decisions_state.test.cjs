const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
function fixture() {
  const nodes = new Map();
  let writes = 0,
    working = false;
  class E extends EventTarget {
    constructor() {
      super();
      this.children = [];
      this.value = "";
      this.hidden = false;
      this.disabled = false;
      this.dataset = {};
      this.style = {};
    }
    append(...v) {
      this.children.push(...v);
    }
    replaceChildren(...v) {
      this.children = v;
    }
    setAttribute() {}
    focus() {}
    querySelectorAll() {
      return [];
    }
    scrollIntoView() {}
  }
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new E());
      return nodes.get(id);
    },
    createElement() {
      return new E();
    },
  };
  let state = {
    connected: true,
    canEdit: true,
    reference: "a".repeat(20),
    epoch: 1,
    enabled: true,
  };
  const api = {
    getState: () => state,
    read: async () => ({
      cases: [
        {
          id: "credit",
          title: "Credit",
          message: "Fictional credit",
          mode: "text",
        },
      ],
      comparisons: [],
      summary: {},
      labels: {},
      budget: { stopped: false },
    }),
    write: async () => {
      writes++;
      return {};
    },
    setBusy(v) {
      working = v;
    },
    expired() {},
  };
  const context = vm.createContext({
    document,
    console,
    crypto: { randomUUID: () => "request-id-test" },
    Date,
    Intl,
    Number,
    JSON,
    Math,
    Event,
  });
  vm.runInContext(
    readFileSync("reconciliation/assets/decisions.js", "utf8") +
      ";globalThis.factory=createDecisions;",
    context,
  );
  return {
    app: context.factory(api),
    nodes,
    api,
    state,
    get writes() {
      return writes;
    },
    get working() {
      return working;
    },
  };
}
test("opening and changing threshold never starts paid work", async () => {
  const f = fixture();
  await f.app.load();
  assert.equal(f.writes, 0);
  f.nodes.get("decision-threshold").value = "0.9";
  f.nodes.get("decision-threshold").dispatchEvent(new Event("input"));
  assert.equal(f.writes, 0);
  assert.equal(f.app.allowLeave(), true);
});
test("recorded selection and reset do not call providers", async () => {
  const f = fixture();
  await f.app.load();
  f.app.reset();
  assert.equal(f.writes, 0);
  assert.equal(f.nodes.get("decision-results").children.length, 0);
});

test("double clicks cannot duplicate paid work and navigation waits", async () => {
  const f = fixture();
  await f.app.load();
  let finish;
  f.api.write = async () => {
    finish = () => {};
    await new Promise((resolve) => {
      finish = resolve;
    });
    return {};
  };
  let calls = 0;
  const writer = f.api.write;
  f.api.write = async (...args) => {
    calls++;
    return writer(...args);
  };
  f.nodes.get("decision-run").dispatchEvent(new Event("click"));
  f.nodes.get("decision-run").dispatchEvent(new Event("click"));
  assert.equal(calls, 1);
  assert.equal(f.app.allowLeave(), false);
  assert.equal(f.working, true);
  finish();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.app.allowLeave(), true);
  assert.equal(f.working, false);
});
test("stale load after switching statements cannot paint old results", async () => {
  const f = fixture();
  let finish;
  f.api.read = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const p = f.app.load();
  f.state.reference = "b".repeat(20);
  f.app.reset();
  finish({
    cases: [],
    comparisons: [],
    summary: {},
    labels: {},
    budget: { stopped: false },
  });
  await p;
  assert.equal(f.nodes.get("decision-results").children.length, 0);
});

test("low-confidence image region explains why no box is drawn", async () => {
  const f = fixture();
  f.api.read = async () => ({
    cases: [
      {
        id: "visual",
        title: "Visual",
        message: "Fictional image",
        mode: "visual",
      },
    ],
    comparisons: [
      {
        case_id: "visual",
        task: { mode: "visual" },
        results: {
          jev: {
            status: "complete",
            answers: {
              reference_present: { type: "noul", noul: 0.99 },
              reference_location: {
                type: "box",
                confidence: 0.39,
                box: { left: 0.43, top: 0.54, right: 0.63, bottom: 0.56 },
              },
            },
          },
        },
      },
    ],
    summary: {},
    labels: {},
    budget: { stopped: false },
  });
  await f.app.load();
  const text = (n) =>
    [n.textContent || "", ...(n.children || []).map(text)].join(" ");
  assert.match(
    text(f.nodes.get("decision-results")),
    /Region withheld.*39%.*80%/,
  );
  assert.equal(f.nodes.get("decision-box").hidden, true);
  assert.equal(f.writes, 0);
});

test("comparison bars use shared matched results and never turn unknown cost into zero", async () => {
  const f = fixture();
  const stats = (ms, cost, correct) => ({
    median_ms: ms,
    cost_per_1000_successes_usd: cost,
    cost_complete: cost !== null,
    correct,
    total: 80,
    completed: 10,
    attempts: 10,
    scored_cases: 10,
    complete_cases: 9,
    claims: {},
    known_cost_usd: 0,
  });
  const base = {
    cases: [
      { id: "credit", title: "Credit", message: "Fictional", mode: "text" },
    ],
    comparisons: [],
    labels: {},
    budget: { stopped: false },
    matched_cases: Array(10).fill("credit"),
  };
  f.api.read = async () => ({
    ...base,
    matched_summary: {
      jev: stats(800, 0.03, 79),
      d1_ultra: stats(780, 0.02, 78),
      gemini: stats(5500, null, 77),
    },
    summary: {},
  });
  await f.app.load();
  const all = (n) => [n, ...(n.children || []).flatMap(all)];
  const nodes = all(f.nodes.get("decision-summary"));
  const bars = nodes.filter((n) => n.className === "decision-bar");
  assert.equal(bars.length, 8);
  assert.ok(
    bars.every(
      (n) =>
        Number.parseFloat(n.style.width) >= 0 &&
        Number.parseFloat(n.style.width) <= 100,
    ),
  );
  assert.equal(bars[0].style.width, (800 / 5500) * 100 + "%");
  const text = (n) =>
    [n.textContent || "", ...(n.children || []).map(text)].join(" ");
  assert.match(
    text(f.nodes.get("decision-summary")),
    /Unknown.*usage incomplete/,
  );
  assert.match(text(f.nodes.get("decision-summary")), /Lower is faster/);
  assert.match(text(f.nodes.get("decision-summary")), /Higher agrees more/);
  assert.match(text(f.nodes.get("decision-summary")), /0\.80 s/);
  assert.match(text(f.nodes.get("decision-summary")), /5\.50 s/);
  assert.equal(f.writes, 0);
});

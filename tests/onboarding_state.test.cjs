const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
function fixture({
  needsConsent = false,
  width = 390,
  evidenceRect = null,
} = {}) {
  const nodes = new Map(),
    stored = new Map(),
    document = new EventTarget();
  let preparations = 0,
    focused = 0,
    assistantShown = 0,
    answerHidden = false;
  class Element extends EventTarget {
    constructor(id) {
      super();
      this.id = id;
      this.hidden = false;
      this.disabled = false;
      this.style = {};
      this.dataset = {};
      this.classList = { add() {}, remove() {} };
      this.children = [];
    }
    getClientRects() {
      return this.id === "conversation-list" && answerHidden ? [] : [{}];
    }
    getBoundingClientRect() {
      if (this.id === "invoice-workspace" && evidenceRect) return evidenceRect;
      return { left: 20, top: 80, right: 350, bottom: 160, height: 80 };
    }
    get offsetHeight() {
      return 200;
    }
    get offsetWidth() {
      return Number.parseFloat(this.style.width) || 360;
    }
    querySelectorAll() {
      return [];
    }
    closest() {
      return null;
    }
    matches() {
      return false;
    }
    scrollIntoView() {
      this.scrolls = (this.scrolls || 0) + 1;
    }
    focus() {
      document.activeElement = this;
    }
    click() {
      this.dispatchEvent(new Event("click", { cancelable: true }));
    }
    append(...items) {
      this.children.push(...items);
    }
    replaceChildren(...items) {
      this.children = items;
    }
    setAttribute() {}
  }
  document.getElementById = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  document.createElement = () => new Element("created");
  document.querySelector = (selector) =>
    document.getElementById(selector.replace(/^#/, "").replace(/^\./, ""));
  document.documentElement = { classList: { add() {}, remove() {} } };
  document.activeElement = document.getElementById("main");
  const state = {
    connected: true,
    reference: "original",
    needsConsent,
    hasPreview: false,
    busy: false,
    invoiceRef: "INV-0002",
    questionsEnabled: true,
  };
  const api = {
    getState: () => state,
    closeHelp() {},
    showUpload() {},
    showAssistant() {
      assistantShown++;
      answerHidden = false;
    },
    showStatement() {},
    focusQuestion() {},
    prepareFirstQuestion() {},
    prepareSample: async () => {
      preparations++;
      state.hasPreview = true;
      fire("workspace-statement-previewed", {});
    },
    focusGuideInvoice: async () => {
      focused++;
    },
    showFollowup: async () => {},
    showTimeline() {},
    showQuality() {},
    openHelp() {},
  };
  const context = vm.createContext({
    document,
    window: new EventTarget(),
    innerWidth: width,
    innerHeight: 844,
    Event,
    getComputedStyle: () => ({
      borderTopLeftRadius: "10px",
      borderTopRightRadius: "10px",
      borderBottomRightRadius: "10px",
      borderBottomLeftRadius: "10px",
    }),
    requestAnimationFrame: (fn) => setImmediate(fn),
    localStorage: {
      getItem: (key) => stored.get(key),
      setItem: (key, value) => stored.set(key, value),
    },
  });
  vm.runInContext(
    readFileSync("reconciliation/assets/onboarding.js", "utf8"),
    context,
  );
  const tour = context.createOnboarding(api);
  function fire(type, detail) {
    const e = new Event(type);
    e.detail = detail;
    document.dispatchEvent(e);
  }
  return {
    tour,
    state,
    stored,
    fire,
    assistantShown: () => assistantShown,
    hideAnswer: () => {
      answerHidden = true;
      context.window.dispatchEvent(new Event("resize"));
    },
    resize: (w, h = 844) => {
      context.innerWidth = w;
      context.innerHeight = h;
      context.window.dispatchEvent(new Event("resize"));
    },
    node: (id) => document.getElementById(id),
    preparations: () => preparations,
    focused: () => focused,
  };
}
async function until(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(setImmediate);
  }
  assert.ok(check(), "Guide did not reach expected state");
}
async function start(f) {
  f.tour.offer();
  f.node("tour-next").click();
  await until(
    () =>
      f.node("tour-card").dataset.phase === "sample" &&
      !f.node("tour-next").disabled,
  );
}
async function confirmed(f) {
  await start(f);
  f.state.hasPreview = true;
  f.fire("workspace-statement-previewed", {});
  await until(
    () =>
      f.node("tour-card").dataset.phase === "preview" &&
      !f.node("tour-next").disabled,
  );
  f.state.reference = "sample";
  f.fire("workspace-statement-confirmed", { reference: "sample" });
  await until(
    () =>
      f.node("tour-card").dataset.phase === "inspect" &&
      !f.node("tour-next").disabled,
  );
}
async function question(f) {
  await confirmed(f);
  f.fire("workspace-evidence-opened", {
    reference: "sample",
    invoiceRef: "INV-0002",
  });
  await until(
    () =>
      f.node("tour-card").dataset.phase === "compare" &&
      !f.node("tour-next").disabled,
  );
  f.fire("workspace-invoice-question", {
    reference: "sample",
    invoiceRef: "INV-0002",
  });
  await until(
    () =>
      f.node("tour-card").dataset.phase === "ask" &&
      !f.node("tour-next").disabled,
  );
}

test("welcome begins a task without doing the user actions", async () => {
  const f = fixture();
  await start(f);
  assert.equal(f.preparations(), 0);
  assert.equal(f.node("tour-next").hidden, true);
  assert.match(f.node("tour-step").textContent, /1 OF 4/);
});
test("actual preview and confirmation advance within chapter one, not Next", async () => {
  const f = fixture();
  await confirmed(f);
  assert.equal(f.focused(), 1);
  assert.match(f.node("tour-step").textContent, /2 OF 4/);
  assert.equal(f.node("tour-next").hidden, true);
});
test("sample consent precedes replacing current work", async () => {
  const f = fixture({ needsConsent: true });
  await start(f);
  f.node("use-sample").click();
  await until(() => f.node("tour-card").dataset.mode === "consent");
  assert.equal(f.preparations(), 0);
  f.node("tour-next").click();
  await until(() => f.node("tour-card").dataset.phase === "preview");
  assert.equal(f.preparations(), 1);
});
test("opening evidence leaves time to read it before asking", async () => {
  const f = fixture();
  await confirmed(f);
  f.fire("workspace-evidence-opened", {
    reference: "sample",
    invoiceRef: "INV-0002",
  });
  await until(() => f.node("tour-card").dataset.phase === "compare");
  assert.match(f.node("tour-step").textContent, /2 OF 4/);
  assert.equal(f.node("tour-next").hidden, true);
});
test("a saved answer does not skip citation inspection", async () => {
  const f = fixture();
  await question(f);
  f.fire("workspace-question-finished", {
    reference: "sample",
    complete: true,
    invoiceRefs: ["INV-0002"],
  });
  await until(() => f.node("tour-card").dataset.phase === "answer");
  assert.match(f.node("tour-step").textContent, /3 OF 4/);
  f.fire("workspace-citation-opened", {
    reference: "sample",
    invoiceRef: "INV-0002",
  });
  await until(() => f.node("tour-card").dataset.phase === "followup-open");
  assert.match(f.node("tour-step").textContent, /4 OF 4/);
});
test("unrelated records and failed answers never earn completion", async () => {
  const f = fixture();
  await question(f);
  f.fire("workspace-question-finished", {
    reference: "other",
    complete: true,
    invoiceRefs: ["INV-0002"],
  });
  f.fire("workspace-question-finished", {
    reference: "sample",
    complete: false,
    invoiceRefs: [],
  });
  await new Promise(setImmediate);
  assert.equal(f.node("tour-card").dataset.phase, "ask");
  assert.equal(f.node("tour-back").hidden, false);
});
test("AI can be skipped explicitly without claiming it answered", async () => {
  const f = fixture();
  await question(f);
  f.node("tour-back").click();
  await until(() => f.node("tour-card").dataset.phase === "followup-open");
  f.fire("workspace-followup-opened", {
    reference: "sample",
    invoiceRef: "INV-0002",
  });
  await until(() => f.node("tour-card").dataset.phase === "record");
  f.fire("workspace-case-saved", {
    reference: "sample",
    invoiceRef: "INV-0002",
    status: "waiting",
  });
  await until(() => f.node("tour-card").dataset.mode === "complete");
  assert.match(f.node("tour-body").textContent, /skipped/i);
});
test("only a saved Waiting for hotel action completes the story", async () => {
  const f = fixture();
  await question(f);
  f.node("tour-back").click();
  await until(() => f.node("tour-card").dataset.phase === "followup-open");
  f.fire("workspace-followup-opened", {
    reference: "sample",
    invoiceRef: "INV-0002",
  });
  await until(() => f.node("tour-card").dataset.phase === "record");
  f.fire("workspace-case-saved", {
    reference: "sample",
    invoiceRef: "OTHER",
    status: "waiting",
  });
  assert.equal(f.node("tour-card").dataset.phase, "record");
  f.fire("workspace-case-saved", {
    reference: "sample",
    invoiceRef: "INV-0002",
    status: "resolved",
  });
  assert.equal(f.node("tour-card").dataset.phase, "record");
  f.fire("workspace-case-saved", {
    reference: "sample",
    invoiceRef: "INV-0002",
    status: "waiting",
  });
  await until(() => f.node("tour-card").dataset.mode === "complete");
  assert.equal(
    JSON.parse(f.stored.get("supplier-workspace-tour-v3")).status,
    "completed",
  );
});
test("unfinished task can be offered after connection reset and dismissed task stays dismissed", async () => {
  const f = fixture();
  await start(f);
  f.tour.stop(null);
  f.tour.offer();
  assert.equal(f.node("workspace-tour").hidden, false);
  f.node("tour-close").click();
  f.tour.stop(null);
  f.tour.offer();
  assert.equal(f.node("workspace-tour").hidden, true);
  f.tour.start();
  assert.equal(f.node("workspace-tour").hidden, false);
});

test("tablet evidence guide docks below the invoice controls when no side fits", async () => {
  const f = fixture({
    width: 798,
    evidenceRect: { left: 12, top: 80, right: 786, bottom: 820, height: 740 },
  });
  await confirmed(f);
  assert.equal(
    f.node("tour-card").style.top,
    "628px",
    "Card must not fall back onto the top-left invoice controls",
  );
  const ring = f.node("tour-spotlight").style;
  assert.ok(
    parseFloat(ring.top) + parseFloat(ring.height) <= 614,
    "Spotlight stays above the docked explanation",
  );
});
test("wide evidence view retains a card beside its target", async () => {
  const f = fixture({
    width: 1280,
    evidenceRect: { left: 20, top: 80, right: 800, bottom: 820, height: 740 },
  });
  await confirmed(f);
  assert.equal(f.node("tour-card").style.left, "820px");
});

test("desktop sidebar layout fits the guide beside the invoice rather than docking over the workspace", async () => {
  const f = fixture({
    width: 1280,
    evidenceRect: { left: 232, top: 30, right: 887, bottom: 870, height: 840 },
  });
  await confirmed(f);
  assert.equal(f.node("tour-card").dataset.docked, "false");
  assert.equal(f.node("tour-card").style.left, "907px");
  assert.equal(f.node("tour-card").style.width, "320px");
});

test("changing width brings the current tour target back into view without jumping on keyboard-height changes", async () => {
  const f = fixture({ width: 1280 });
  await confirmed(f);
  const target = f.node("invoice-workspace"),
    before = target.scrolls || 0;
  f.resize(390);
  await new Promise(setImmediate);
  assert.equal(target.scrolls, before + 1);
  f.resize(390, 600);
  await new Promise(setImmediate);
  assert.equal(target.scrolls, before + 1);
});

test("answer stage opens Assistant before finding its citation and offers recovery if hidden later", async () => {
  const f = fixture();
  await question(f);
  const before = f.assistantShown();
  f.fire("workspace-question-finished", {
    reference: "sample",
    complete: true,
    invoiceRefs: ["INV-0002"],
  });
  await until(
    () =>
      f.node("tour-card").dataset.phase === "answer" &&
      !f.node("tour-next").disabled,
  );
  assert.equal(
    f.assistantShown(),
    before + 1,
    "The answer must be made visible before the tour points to it",
  );
  f.hideAnswer();
  await new Promise(setImmediate);
  assert.equal(
    f.node("tour-next").hidden,
    false,
    "A hidden target must leave a way to return",
  );
  assert.equal(f.node("tour-next").textContent, "Show answer");
  f.node("tour-next").click();
  await until(
    () => f.assistantShown() === before + 2 && !f.node("tour-next").disabled,
  );
  assert.equal(f.node("tour-next").hidden, true);
});

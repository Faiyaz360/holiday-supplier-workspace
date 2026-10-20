const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
function fixture() {
  const nodes = new Map(),
    writes = [];
  let confirm = false;
  class Element extends EventTarget {
    constructor() {
      super();
      this.children = [];
      this.dataset = {};
      this.value = "";
      this.hidden = false;
      this.disabled = false;
    }
    append(...items) {
      this.children.push(...items);
    }
    replaceChildren(...items) {
      this.children = items;
    }
    focus() {}
    scrollIntoView() {}
    querySelectorAll(selector) {
      return this.children.flatMap((n) => [
        ...((n.className || "").split(" ").includes(selector.slice(1))
          ? [n]
          : []),
        ...n.querySelectorAll(selector),
      ]);
    }
  }
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new Element());
      return nodes.get(id);
    },
    createElement: () => new Element(),
  };
  const node = (id) => document.getElementById(id),
    state = {
      connected: true,
      reference: "a".repeat(20),
      epoch: 1,
      canEdit: true,
      modelEnabled: true,
    };
  const record = {
    request_id: "reply-example",
    filename: "hotel.txt",
    text: "INV-0002 needs a credit check.",
    sha256: "f".repeat(64),
    status: "finished",
    applied: {},
    outcome: {
      status: "complete",
      model_calls: 3,
      proposals: [
        {
          invoice_ref: "INV-0002",
          evidence_id: "R-0002",
          quote: "INV-0002 needs a credit check.",
          summary: "Statement EUR 239.00; ledger EUR 214.00",
          next_step: "Check the credit record before concluding.",
          suggested_status: "checking",
        },
      ],
    },
  };
  const api = {
    getState: () => state,
    setBusy(value) {
      state.canEdit = !value;
    },
    isVisible: () => true,
    onRecorded() {},
    expired() {},
    openEvidence() {},
    read: async (path) =>
      path.endsWith("/cases")
        ? { generation: 0, history: [] }
        : { replies: [record] },
    write: async (path, body) => {
      writes.push({ path, body });
      if (path.endsWith("/apply"))
        record.applied = { 0: { status: "checking" } };
      return { reply: record };
    },
  };
  const context = vm.createContext({
    document,
    window: { confirm: () => confirm },
    crypto: require("node:crypto").webcrypto,
    TextDecoder,
  });
  vm.runInContext(
    readFileSync("reconciliation/assets/responses.js", "utf8"),
    context,
  );
  const ui = context.createReplies(api);
  return { ui, node, writes, state, allowDiscard: () => (confirm = true) };
}
async function settled(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(setImmediate);
  }
  assert.ok(check());
}
test("analysis enables review after busy ends and never confirms its own proposal", async () => {
  const f = fixture();
  f.node("reply-text").value = "INV-0002 needs a credit check.";
  f.node("reply-filename").value = "hotel.txt";
  f.node("reply-form").dispatchEvent(new Event("submit", { cancelable: true }));
  await settled(
    () => f.writes.length === 1 && !f.node("analyse-reply").disabled,
  );
  const button = f.node("reply-list").querySelectorAll(".reply-review")[0];
  assert.ok(button);
  assert.equal(button.disabled, false);
  assert.equal(f.writes.length, 1);
  button.dispatchEvent(new Event("click"));
  await settled(() => f.node("reply-note").value.length > 0);
  assert.equal(f.writes.length, 1, "Choosing a proposal must not write a case");
  f.node("reply-actor").value = "Fictional verifier";
  f.node("reply-status").value = "checking";
  f.node("reply-action-form").dispatchEvent(
    new Event("submit", { cancelable: true }),
  );
  await settled(
    () => f.writes.length === 2 && !f.node("analyse-reply").disabled,
  );
  assert.match(f.writes[1].path, /apply$/);
  assert.equal(
    f.node("reply-list").querySelectorAll(".reply-review")[0].disabled,
    true,
  );
});
test("declining to leave retains the unsaved hotel reply", () => {
  const f = fixture();
  f.node("reply-text").value = "Unsaved original hotel correspondence";
  f.node("reply-text").dispatchEvent(new Event("input"));
  assert.equal(f.ui.allowLeave(), false);
  assert.equal(
    f.node("reply-text").value,
    "Unsaved original hotel correspondence",
  );
});

test("accepting draft discard clears only unsaved response input", () => {
  const f = fixture();
  f.node("reply-text").value = "Unsaved correspondence";
  f.node("reply-text").dispatchEvent(new Event("input"));
  f.allowDiscard();
  assert.equal(f.ui.allowLeave(), true);
  assert.equal(f.node("reply-text").value, "");
  assert.equal(f.writes.length, 0);
});

test("UTF-8 reply file loads original text without a model request", async () => {
  const f = fixture(),
    text = "Hotel correspondence for INV-0002: please check the credit.";
  const bytes = new TextEncoder().encode(text);
  f.node("reply-file").files = [
    {
      name: "hotel.txt",
      size: bytes.length,
      arrayBuffer: async () => bytes.buffer,
    },
  ];
  f.node("reply-file").dispatchEvent(new Event("change"));
  await settled(() => f.node("reply-text").value === text);
  assert.equal(f.node("reply-filename").value, "hotel.txt");
  assert.equal(f.writes.length, 0);
});
test("invalid UTF-8 reply file leaves current text intact", async () => {
  const f = fixture();
  f.node("reply-text").value = "Existing draft";
  const bytes = new Uint8Array([255]);
  f.node("reply-file").files = [
    { name: "hotel.txt", size: 1, arrayBuffer: async () => bytes.buffer },
  ];
  f.node("reply-file").dispatchEvent(new Event("change"));
  await settled(() => f.node("reply-feedback").dataset.tone === "error");
  assert.equal(f.node("reply-text").value, "Existing draft");
  assert.equal(f.writes.length, 0);
});

const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { readFileSync } = require("node:fs");
function fixture() {
  const frame = {},
    events = [],
    input = { value: "", focus() {} },
    nodes = {
      "evidence-frame": { contentWindow: frame },
      "question-input": input,
    };
  let receive;
  const source = readFileSync("reconciliation/assets/hosted.js", "utf8");
  const start = source.indexOf('  window.addEventListener("message",');
  const end = source.indexOf('  el("suggest-invoice")', start);
  const context = vm.createContext({
    window: {
      addEventListener(type, handler) {
        receive = handler;
      },
    },
    el: (id) => nodes[id],
    evidenceToken: "current-load",
    evidenceReady: false,
    openedEvidence: "",
    focusedEvidence: "",
    busy: false,
    runId: "sample",
    saved: { result: { rows: [{ invoice_ref: "INV-0002" }] } },
    casework: { showStatement: () => true },
    walkthrough: { isActive: () => false },
    selectTab() {},
    questionFeedback() {},
    guideEvent: (type, detail) => events.push({ type, detail }),
  });
  vm.runInContext(source.slice(start, end), context);
  const data = {
    type: "invoice-evidence-opened",
    token: "current-load",
    invoiceRef: "INV-0002",
  };
  return {
    context,
    events,
    input,
    send: (changes = {}, payload = {}) =>
      receive({
        source: frame,
        origin: "null",
        data: { ...data, ...payload },
        ...changes,
      }),
  };
}
test("frame messages reject the wrong source, origin, token, shape and invoice", () => {
  const f = fixture();
  f.send({ origin: "https://untrusted.example" });
  f.send({ source: {} });
  f.send({}, { token: "old-load" });
  f.send({}, { extra: true });
  f.send({}, { invoiceRef: "UNKNOWN" });
  f.send({}, { type: "execute" });
  assert.equal(f.events.length, 0);
  assert.equal(f.context.openedEvidence, "");
  assert.equal(f.input.value, "");
});
test("focus acknowledgement is not evidence inspection and Ask only prepares a question", () => {
  const f = fixture();
  f.send({}, { type: "invoice-focused" });
  assert.equal(f.context.focusedEvidence, "INV-0002");
  assert.equal(f.events.length, 0);
  f.send();
  assert.equal(f.events[0].type, "workspace-evidence-opened");
  f.send({}, { type: "ask-invoice" });
  assert.match(f.input.value, /INV-0002/);
  assert.equal(f.events[1].type, "workspace-invoice-question");
  assert.equal(f.events[1].detail.reference, "sample");
});

function nativeEvidenceFixture() {
  class Element extends EventTarget {
    constructor() {
      super();
      this.children = [];
      this.hidden = false;
      this.disabled = false;
      this.dataset = {};
      this.open = false;
    }
    append(child) {
      this.children.push(child);
    }
    setAttribute() {}
  }
  const messages = [],
    details = new Element(),
    bannerButton = new Element();
  const row = {
    dataset: { invoiceRef: "INV-0002" },
    querySelector: () => details,
  };
  const document = {
    body: {
      dataset: {
        parentOrigin: "https://workspace.example",
        evidenceToken: "load-token",
      },
    },
    getElementById: () => bannerButton,
    createElement: () => new Element(),
  };
  const source = readFileSync("reconciliation/assets/workspace.js", "utf8");
  const start = source.indexOf("// Every embedded invoice");
  const end = source.indexOf("const sectionLinks", start);
  const parent = {
    postMessage: (data, origin) => messages.push({ data, origin }),
  };
  vm.runInNewContext(source.slice(start, end), {
    document,
    rows: [row],
    window: { parent },
    Event,
  });
  details.open = true;
  details.dispatchEvent(new Event("toggle"));
  return { details, messages };
}
test("ordinary evidence opening exposes its own Ask action without citation focus", () => {
  const f = nativeEvidenceFixture();
  const button = f.details.children.find(
    (node) => node.textContent === "Ask about this invoice",
  );
  assert.ok(
    button,
    "Expanded evidence must include the promised Ask button even in All invoices view",
  );
  button.dispatchEvent(new Event("click"));
  assert.equal(f.messages.at(-1).data.type, "ask-invoice");
  assert.equal(f.messages.at(-1).data.invoiceRef, "INV-0002");
  assert.equal(f.messages.at(-1).origin, "https://workspace.example");
});

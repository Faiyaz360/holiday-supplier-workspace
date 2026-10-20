"use strict";
const search = document.getElementById("invoice-search");
const filter = document.getElementById("invoice-filter");
const rows = Array.from(document.querySelectorAll("#invoice-rows > tr"));
const count = document.getElementById("visible-count");
const empty = document.getElementById("empty-state");
const draft = document.getElementById("draft-text");
const feedback = document.getElementById("draft-feedback");
function applyFilters() {
  const term = search.value.trim().toLocaleLowerCase();
  let shown = 0;
  for (const row of rows) {
    const status = row.dataset.status;
    const statusMatches =
      filter.value === "all" ||
      (filter.value === "exceptions"
        ? status !== "matched"
        : status === filter.value);
    row.hidden = !(
      statusMatches && row.textContent.toLocaleLowerCase().includes(term)
    );
    if (!row.hidden) shown += 1;
  }
  count.textContent = `${shown} of ${rows.length} invoice groups`;
  empty.hidden = shown !== 0;
}
search.addEventListener("input", applyFilters);
filter.addEventListener("change", applyFilters);
document.getElementById("reset-filters").addEventListener("click", () => {
  search.value = "";
  filter.value = "exceptions";
  applyFilters();
  search.focus();
});
document.getElementById("copy-draft").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(draft.textContent);
    feedback.textContent = "Draft copied. Nothing has been sent.";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(draft);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    draft.scrollIntoView({ block: "center" });
    feedback.textContent =
      "Automatic copying is unavailable. The draft is selected; use your device’s copy command.";
  }
});
document.getElementById("download-draft").addEventListener("click", () => {
  const blob = new Blob([draft.textContent], {
    type: "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "supplier-query-draft.txt";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  feedback.textContent =
    "Draft exported as a text file. Nothing has been sent.";
});
document
  .getElementById("print-report")
  .addEventListener("click", () => window.print());
applyFilters();

// Every embedded invoice keeps its Ask action with its own source evidence.
function evidenceNotice(type, invoiceRef) {
  if (
    window.parent !== window &&
    document.body.dataset.parentOrigin &&
    document.body.dataset.evidenceToken
  )
    window.parent.postMessage(
      { type, token: document.body.dataset.evidenceToken, invoiceRef },
      document.body.dataset.parentOrigin,
    );
}
for (const row of rows) {
  const details = row.querySelector("details.evidence");
  if (!details) continue;
  if (
    window.parent !== window &&
    document.body.dataset.parentOrigin &&
    document.body.dataset.evidenceToken
  ) {
    const ask = document.createElement("button");
    ask.type = "button";
    ask.className = "btn primary";
    ask.textContent = "Ask about this invoice";
    ask.addEventListener("click", () => {
      if (details.open) evidenceNotice("ask-invoice", row.dataset.invoiceRef);
    });
    details.append(ask);
  }
  details.addEventListener("toggle", () => {
    if (details.open)
      evidenceNotice("invoice-evidence-opened", row.dataset.invoiceRef);
  });
}

const sectionLinks = Array.from(document.querySelectorAll(".nav-link"));
function updateCurrentSection() {
  const current =
    sectionLinks.find(
      (link) => link.getAttribute("href") === window.location.hash,
    ) || sectionLinks[0];
  for (const link of sectionLinks) {
    if (link === current) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}
window.addEventListener("hashchange", updateCurrentSection);
updateCurrentSection();

document.getElementById("citation-show-all").addEventListener("click", () => {
  document.body.classList.remove("evidence-focus");
  document.getElementById("citation-focus-banner").hidden = true;
  search.value = "";
  filter.value = "exceptions";
  applyFilters();
  search.focus();
});

// Only this frame's parent, with its per-load token and exact origin, can open evidence.
window.addEventListener("message", (event) => {
  const data = event.data;
  if (
    window.parent === window ||
    event.source !== window.parent ||
    !document.body.dataset.parentOrigin ||
    event.origin !== document.body.dataset.parentOrigin ||
    !data ||
    typeof data !== "object" ||
    Object.keys(data).length !== 3 ||
    !["open-invoice-evidence", "focus-invoice"].includes(data.type) ||
    typeof data.token !== "string" ||
    data.token !== document.body.dataset.evidenceToken ||
    typeof data.invoiceRef !== "string" ||
    data.invoiceRef.length > 200
  )
    return;
  const row = rows.find((item) => item.dataset.invoiceRef === data.invoiceRef);
  if (!row) return;
  search.value = data.invoiceRef;
  filter.value = "all";
  applyFilters();
  for (const item of rows) item.hidden = item !== row;
  count.textContent = `Showing cited invoice · 1 of ${rows.length} invoice groups`;
  empty.hidden = true;
  const details = row.querySelector("details.evidence"),
    summary = details?.querySelector("summary");
  if (!details || !summary) return;
  document.body.classList.add("evidence-focus");
  document.getElementById("citation-focus-banner").hidden = false;
  document.getElementById("citation-focus-label").textContent =
    `Source evidence · ${data.invoiceRef}`;
  details.open = data.type === "open-invoice-evidence";
  summary.focus({ preventScroll: true });
  row.scrollIntoView({
    block: "start",
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
  window.parent.postMessage(
    {
      type: details.open ? "invoice-evidence-opened" : "invoice-focused",
      token: data.token,
      invoiceRef: data.invoiceRef,
    },
    event.origin,
  );
});

if (
  window.parent !== window &&
  document.body.dataset.parentOrigin &&
  document.body.dataset.evidenceToken
) {
  window.parent.postMessage(
    { type: "evidence-ready", token: document.body.dataset.evidenceToken },
    document.body.dataset.parentOrigin,
  );
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape")
      window.parent.postMessage(
        { type: "evidence-escape", token: document.body.dataset.evidenceToken },
        document.body.dataset.parentOrigin,
      );
  });
}

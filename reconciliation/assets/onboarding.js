function createOnboarding(api) {
  const el = (id) => document.getElementById(id);
  const root = el("workspace-tour"),
    card = el("tour-card"),
    ring = el("tour-spotlight");
  const shades = [
    "tour-shade-top",
    "tour-shade-bottom",
    "tour-shade-left",
    "tour-shade-right",
  ].map(el);
  const preferenceKey = "supplier-workspace-tour-v3";
  let active = false,
    offered = false,
    mode = "welcome",
    index = 0,
    phase = "sample",
    working = false;
  let generation = 0,
    target = null,
    frame = 0,
    observer = null,
    returnFocus = null,
    consent = false;
  let sampleReference = "",
    invoice = "",
    assistantChecked = false;
  const stages = {
    sample: {
      chapter: 0,
      title: "A hotel has sent its statement.",
      body: "Start with the Mallorca sample. Press Use sample statement to check the file before saving anything.",
      target: "#use-sample",
      enter: () => api.showUpload(),
    },
    preview: {
      chapter: 0,
      title: "Check it before you save it.",
      body: "Look over the preview, then press Confirm and reconcile. This compares the records without using Gemini.",
      target: "#confirm-import",
      enter: () => api.showUpload(),
    },
    inspect: {
      chapter: 1,
      title: "One invoice needs checking.",
      body: "Open View evidence for this invoice. Compare the statement with the finance record; a difference alone does not tell us who is right.",
      target: ".invoice-workspace",
      enter: () => api.focusGuideInvoice(),
    },
    compare: {
      chapter: 1,
      title: "Follow the difference to its source.",
      body: "Read the two balances and their CSV rows. When you are ready, press Ask about this invoice inside this invoice’s expanded evidence.",
      target: ".invoice-workspace",
    },
    ask: {
      chapter: 2,
      title: "Ask what needs checking.",
      body: "Your invoice question is ready to edit. Press Ask assistant to get an answer linked to these records. This makes a Gemini request.",
      target: "#question-form",
      enter: () => api.showAssistant(),
    },
    answer: {
      chapter: 2,
      title: "Check the answer against its evidence.",
      body: "Read the saved answer, then press its invoice evidence link. The citation takes you back to the original source rows.",
      target: () =>
        api.findCitation
          ? api.findCitation(invoice)
          : document.querySelector("#conversation-list"),
      enter: () => api.showAssistant(),
    },
    "followup-open": {
      chapter: 3,
      title: "Record the next action.",
      body: "You have checked the difference. Open Follow-up to leave a clear next step for the hotel accounts team.",
      target: "#view-followup",
    },
    record: {
      chapter: 3,
      title: "Leave the hotel a clear next step.",
      body: "Choose Waiting for hotel, add your name and a short note about what needs confirming, then press Save update. This records follow-up; it sends nothing.",
      target: "#case-form",
    },
  };
  function remembered() {
    try {
      const value = JSON.parse(localStorage.getItem(preferenceKey));
      return (
        value?.version === 3 &&
        ["dismissed", "completed"].includes(value.status)
      );
    } catch {
      return false;
    }
  }
  function remember(status) {
    try {
      localStorage.setItem(
        preferenceKey,
        JSON.stringify({ version: 3, status }),
      );
    } catch {
      /* A preference must not block the task. */
    }
  }
  function paintRect(node, left, top, width, height) {
    Object.assign(node.style, {
      left: `${left}px`,
      top: `${top}px`,
      width: `${Math.max(0, width)}px`,
      height: `${Math.max(0, height)}px`,
    });
  }
  function shadeAll() {
    for (const shade of shades) shade.style.background = "transparent";
    shades[0].style.background = "rgba(11,35,38,.62)";
    paintRect(shades[0], 0, 0, innerWidth, innerHeight);
    for (const shade of shades.slice(1)) paintRect(shade, 0, 0, 0, 0);
    ring.hidden = true;
  }
  function position() {
    if (!active) return;
    const width = innerWidth,
      height = innerHeight,
      phone = width < 650;
    card.dataset.mode = mode;
    if (mode !== "step" || !target || !target.getClientRects().length) {
      document.documentElement.classList.remove("tour-docked");
      card.dataset.docked = "false";
      shadeAll();
      if (mode === "step" && !working) {
        el("tour-next").hidden = false;
        el("tour-next").textContent =
          phase === "answer" ? "Show answer" : "Return to this action";
      }
      card.style.width = `${Math.min(420, width - 32)}px`;
      card.style.left = `${Math.max(16, (width - Math.min(420, width - 32)) / 2)}px`;
      card.style.top = `${Math.max(16, (height - card.offsetHeight) / 2)}px`;
      return;
    }
    el("tour-next").hidden = true;
    card.style.width = `${Math.min(width >= 1100 ? 320 : 360, width - 32)}px`;
    const box = target.getBoundingClientRect(),
      cardWidth = card.offsetWidth,
      cardHeight = card.offsetHeight;
    const docked =
      phone ||
      (width - box.right <= cardWidth + 40 && box.left <= cardWidth + 40);
    card.dataset.docked = String(docked);
    document.documentElement.classList[docked ? "add" : "remove"](
      "tour-docked",
    );
    let left, top;
    if (docked) {
      left = 16;
      top = Math.max(16, height - cardHeight - 16);
    } else if (width - box.right > cardWidth + 40) {
      left = box.right + 20;
      top = Math.max(16, Math.min(box.top, height - cardHeight - 16));
    } else if (box.left > cardWidth + 40) {
      left = box.left - cardWidth - 20;
      top = Math.max(16, Math.min(box.top, height - cardHeight - 16));
    } else {
      left = Math.max(16, Math.min(box.left, width - cardWidth - 16));
      top =
        box.bottom + 20 + cardHeight < height
          ? box.bottom + 20
          : Math.max(16, box.top - cardHeight - 20);
    }
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
    let x = Math.max(8, box.left - 7),
      y = Math.max(8, box.top - 7);
    let right = Math.min(width - 8, box.right + 7),
      bottom = Math.min(height - 8, box.bottom + 7);
    // When no side fits, keep invoice controls above the bottom explanation card.
    if (docked) {
      bottom = Math.min(bottom, top - 14);
      y = Math.min(y, Math.max(8, bottom - 60));
    }
    if (bottom <= y || right <= x) {
      shadeAll();
      return;
    }
    const corners = getComputedStyle(target);
    for (const corner of [
      "borderTopLeftRadius",
      "borderTopRightRadius",
      "borderBottomRightRadius",
      "borderBottomLeftRadius",
    ]) {
      const [horizontal, vertical = horizontal] = corners[corner].split(/\s+/);
      ring.style[corner] = `calc(${horizontal} + 7px) calc(${vertical} + 7px)`;
    }
    // The rounded shadow paints the dim area; transparent panels only catch outside clicks.
    for (const shade of shades) shade.style.background = "transparent";
    ring.hidden = false;
    paintRect(ring, x, y, right - x, bottom - y);
    paintRect(shades[0], 0, 0, width, y);
    paintRect(shades[1], 0, bottom, width, height - bottom);
    paintRect(shades[2], 0, y, x, bottom - y);
    paintRect(shades[3], right, y, width - right, bottom - y);
  }
  function schedulePosition() {
    if (!active || frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      position();
    });
  }

  function buttons() {
    card.dataset.phase = phase;
    el("tour-next").hidden = mode === "step";
    el("tour-next").disabled = working;
    el("tour-next").textContent = working
      ? "Getting ready…"
      : mode === "welcome"
        ? "Start guided task"
        : mode === "consent"
          ? "Use a separate sample"
          : mode === "complete"
            ? "Open my workspace"
            : "Return to this action";
    el("tour-back").hidden = !(mode === "step" && index === 2);
    el("tour-back").textContent = "Continue without assistant";
    el("tour-back").disabled = working || Boolean(api.getState().busy);
    el("tour-skip").textContent =
      mode === "welcome"
        ? "Explore myself"
        : mode === "complete"
          ? "Close recap"
          : mode === "consent"
            ? "Keep current work"
            : "Skip task";
    el("tour-step").textContent =
      mode === "welcome"
        ? "ONE INVOICE, FROM START TO FOLLOW-UP"
        : mode === "consent"
          ? "KEEP YOUR WORK SAFE"
          : mode === "complete"
            ? "YOUR FIRST CASE, RECORDED"
            : mode === "error"
              ? "YOUR WORK IS SAFE"
              : `CHAPTER ${index + 1} OF 4`;
    el("tour-progress").hidden = !["step", "complete"].includes(mode);
    el("tour-progress-fill").style.width =
      mode === "complete" ? "100%" : `${((index + 1) / 4) * 100}%`;
    el("tour-discoveries").hidden = mode !== "complete";
  }
  function showCopy(title, body) {
    el("tour-title").textContent = title;
    el("tour-body").textContent = body;
    buttons();
    position();
    card.classList.remove("tour-card-enter");
    requestAnimationFrame(() => {
      if (active) card.classList.add("tour-card-enter");
    });
  }
  function present() {
    active = true;
    offered = true;
    mode = "welcome";
    phase = "sample";
    index = 0;
    working = false;
    generation++;
    target = null;
    observer?.disconnect();
    consent = false;
    sampleReference = "";
    invoice = "";
    assistantChecked = false;
    returnFocus = document.activeElement;
    api.closeHelp();
    root.hidden = false;
    document.documentElement.classList.add("tour-active");
    showCopy(
      "A hotel statement. One clear next step.",
      "Work through a fictional Mallorca invoice yourself: load it, check the difference, ask the assistant and record follow-up. Gemini runs only if you press Ask assistant.",
    );
    el("tour-next").focus({ preventScroll: true });
  }
  async function enter(nextPhase) {
    if (!active) return;
    phase = nextPhase;
    index = stages[phase].chapter;
    mode = "step";
    working = true;
    const version = ++generation;
    target = null;
    observer?.disconnect();
    showCopy(
      stages[phase].title,
      phase === "ask" && !api.getState().questionsEnabled
        ? "The assistant is unavailable for this workspace. You can continue without it and record the next action using the source evidence."
        : stages[phase].body,
    );
    try {
      if (stages[phase].enter) await stages[phase].enter();
      if (!active || version !== generation) return;
      target =
        typeof stages[phase].target === "function"
          ? stages[phase].target()
          : document.querySelector(stages[phase].target);
      if (!target || !target.getClientRects().length)
        throw new Error(
          "This control is not ready. You can return to the action or skip the task; saved work stays unchanged.",
        );
      const align =
        target.getBoundingClientRect().height > 200 ? "start" : "center";
      target.scrollIntoView({ block: align, behavior: "auto" });
      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      );
      if (!active || version !== generation) return;
      if (typeof ResizeObserver === "function") {
        observer = new ResizeObserver(schedulePosition);
        observer.observe(target);
      }
      working = false;
      buttons();
      position();
      card.focus({ preventScroll: true });
    } catch (error) {
      if (!active || version !== generation) return;
      mode = "error";
      working = false;
      target = null;
      showCopy(
        "Let’s return to the action.",
        error.message || "The action is not ready. Your work is safe.",
      );
    }
  }
  function matches(event) {
    return (
      active &&
      mode === "step" &&
      event.detail?.reference === sampleReference &&
      event.detail?.invoiceRef === invoice
    );
  }
  function complete() {
    mode = "complete";
    working = false;
    target = null;
    generation++;
    observer?.disconnect();
    remember("completed");
    showCopy(
      "You turned a difference into a next step.",
      `${invoice}: statement saved, source evidence inspected, ${assistantChecked ? "assistant answer checked against its citation" : "assistant skipped"}, and Waiting for hotel recorded with your note. Original balances are unchanged. Nothing was sent.`,
    );
    el("tour-next").focus({ preventScroll: true });
  }
  function stop(status = "dismissed", restoreFocus = true) {
    if (!active) return;
    active = false;
    working = false;
    generation++;
    observer?.disconnect();
    target = null;
    root.hidden = true;
    document.documentElement.classList.remove("tour-active", "tour-docked");
    if (status) remember(status);
    if (restoreFocus) {
      const destination =
        returnFocus?.getClientRects().length && !returnFocus.closest("[hidden]")
          ? returnFocus
          : el("main");
      destination.focus({ preventScroll: true });
    }
  }
  el("tour-next").addEventListener("click", async () => {
    if (!active || working) return;
    if (mode === "welcome") {
      remember("in_progress");
      await enter("sample");
      return;
    }
    if (
      mode === "error" ||
      (mode === "step" && (!target || !target.getClientRects().length))
    ) {
      await enter(phase);
      return;
    }
    if (mode === "complete") {
      stop("completed");
      return;
    }
    if (mode === "consent") {
      consent = true;
      mode = "step";
      working = true;
      buttons();
      const version = generation;
      try {
        await api.prepareSample();
        if (active && phase === "sample" && api.getState().hasPreview)
          await enter("preview");
      } catch (error) {
        if (active && version === generation) {
          mode = "error";
          working = false;
          showCopy("The sample is not ready.", error.message);
        }
      }
    }
  });
  el("use-sample").addEventListener(
    "click",
    (event) => {
      if (
        !active ||
        mode !== "step" ||
        phase !== "sample" ||
        consent ||
        !api.getState().needsConsent
      )
        return;
      event.preventDefault();
      event.stopImmediatePropagation();
      mode = "consent";
      target = null;
      showCopy(
        "Use a separate sample?",
        "Your saved statements and follow-up stay unchanged. This task selects a separate sample and replaces any unsaved CSV preview.",
      );
      el("tour-next").focus({ preventScroll: true });
    },
    true,
  );
  document.addEventListener("workspace-statement-previewed", () => {
    if (
      active &&
      mode === "step" &&
      phase === "sample" &&
      api.getState().hasPreview
    )
      enter("preview");
  });
  document.addEventListener("workspace-statement-confirmed", () => {
    if (!active || mode !== "step" || phase !== "preview") return;
    const state = api.getState();
    sampleReference = state.reference;
    invoice = state.invoiceRef;
    enter("inspect");
  });
  document.addEventListener("workspace-evidence-opened", (event) => {
    if (matches(event) && phase === "inspect") enter("compare");
  });
  document.addEventListener("workspace-invoice-question", (event) => {
    if (matches(event) && phase === "compare") enter("ask");
  });
  document.addEventListener("workspace-question-started", (event) => {
    if (
      active &&
      mode === "step" &&
      phase === "ask" &&
      event.detail?.reference === sampleReference
    ) {
      showCopy(
        "The assistant is checking this invoice.",
        "It is reading the saved evidence. Your original statement stays unchanged. Wait for the saved answer before inspecting its citation.",
      );
    }
  });
  document.addEventListener("workspace-question-finished", (event) => {
    if (
      !active ||
      mode !== "step" ||
      phase !== "ask" ||
      event.detail?.reference !== sampleReference
    )
      return;
    if (event.detail.complete && event.detail.invoiceRefs?.includes(invoice))
      enter("answer");
    else
      showCopy(
        "The answer needs another check.",
        "Use the saved question message to decide whether to retry, or continue without the assistant. No unfinished answer counts as checked.",
      );
  });
  document.addEventListener("workspace-citation-opened", (event) => {
    if (matches(event) && phase === "answer") {
      assistantChecked = true;
      enter("followup-open");
    }
  });
  el("tour-back").addEventListener("click", () => {
    if (
      !active ||
      mode !== "step" ||
      index !== 2 ||
      working ||
      api.getState().busy
    )
      return;
    enter("followup-open");
  });
  document.addEventListener("workspace-followup-opened", (event) => {
    if (matches(event) && phase === "followup-open") enter("record");
  });
  document.addEventListener("workspace-case-saved", (event) => {
    if (
      matches(event) &&
      phase === "record" &&
      event.detail.status === "waiting"
    )
      complete();
  });
  for (const id of ["tour-skip", "tour-close"])
    el(id).addEventListener("click", () =>
      stop(mode === "complete" ? "completed" : "dismissed"),
    );
  for (const [id, action] of [
    ["tour-discover-activity", () => api.showTimeline()],
    ["tour-discover-quality", () => api.showQuality()],
    ["tour-discover-help", () => api.openHelp()],
  ])
    el(id).addEventListener("click", () => {
      stop("completed", false);
      action();
    });
  document.addEventListener("focusin", (event) => {
    if (
      active &&
      mode === "step" &&
      card.dataset.docked === "true" &&
      target?.contains(event.target) &&
      event.target.getBoundingClientRect().bottom >
        card.getBoundingClientRect().top - 14
    ) {
      event.target.scrollIntoView({ block: "center", behavior: "auto" });
      schedulePosition();
    }
  });
  let layoutWidth = innerWidth;
  window.addEventListener("resize", () => {
    if (innerWidth !== layoutWidth) {
      layoutWidth = innerWidth;
      if (active && mode === "step" && target)
        target.scrollIntoView({ block: "start", behavior: "auto" });
    }
    schedulePosition();
  });
  window.addEventListener("scroll", schedulePosition, true);
  window.addEventListener(
    "keydown",
    (event) => {
      if (!active) return;
      if (event.key === "Escape") {
        event.preventDefault();
        stop();
        return;
      }
      if (event.key !== "Tab") return;
      const selectors =
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), iframe, summary, [tabindex="0"]';
      const candidates = [
        ...card.querySelectorAll(selectors),
        ...(target ? [target, ...target.querySelectorAll(selectors)] : []),
      ].filter(
        (node) =>
          node.matches(selectors) &&
          !node.closest("[hidden]") &&
          node.getClientRects().length,
      );
      const choices = [...new Set(candidates)];
      if (!choices.length) return;
      event.preventDefault();
      const current = choices.indexOf(document.activeElement),
        direction = event.shiftKey ? -1 : 1;
      const origin = current < 0 ? (direction > 0 ? -1 : 0) : current;
      choices[(origin + direction + choices.length) % choices.length].focus({
        preventScroll: true,
      });
    },
    true,
  );
  return {
    offer() {
      if (!active && !offered && !remembered() && api.getState().connected)
        present();
    },
    start() {
      if (api.getState().connected) present();
    },
    stop(status = null) {
      stop(status, false);
      if (status === null) offered = false;
    },
    isActive() {
      return active;
    },
  };
}

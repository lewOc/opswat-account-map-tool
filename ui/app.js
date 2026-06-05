const state = {
  current: null,
  currentSummary: null,
  summaries: [],
  capabilities: null,
};

const els = {
  form: document.querySelector("#generateForm"),
  target: document.querySelector("#target"),
  focus: document.querySelector("#focus"),
  provider: document.querySelector("#provider"),
  apiKey: document.querySelector("#apiKey"),
  useCases: document.querySelector("#useCases"),
  dryRun: document.querySelector("#dryRun"),
  generateButton: document.querySelector("#generateButton"),
  generationStatus: document.querySelector("#generationStatus"),
  healthText: document.querySelector("#healthText"),
  statusDot: document.querySelector(".status-dot"),
  newWorkspace: document.querySelector("#newWorkspace"),
  savedMaps: document.querySelector("#savedMaps"),
  accountName: document.querySelector("#accountName"),
  accountSector: document.querySelector("#accountSector"),
  accountSummary: document.querySelector("#accountSummary"),
  metricUseCases: document.querySelector("#metricUseCases"),
  metricSignals: document.querySelector("#metricSignals"),
  metricSources: document.querySelector("#metricSources"),
  metricProducts: document.querySelector("#metricProducts"),
  useCaseList: document.querySelector("#useCaseList"),
  signalList: document.querySelector("#signalList"),
  buyerList: document.querySelector("#buyerList"),
  outreachBlock: document.querySelector("#outreachBlock"),
  evidenceList: document.querySelector("#evidenceList"),
  pptxButton: document.querySelector("#pptxButton"),
  jsonLink: document.querySelector("#jsonLink"),
  mdLink: document.querySelector("#mdLink"),
  loadingOverlay: document.querySelector("#loadingOverlay"),
  loadingTitle: document.querySelector("#loadingTitle"),
  loadingDetail: document.querySelector("#loadingDetail"),
  toast: document.querySelector("#toast"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const timeoutMs = options.timeoutMs ?? 100000;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const { timeoutMs: _timeoutMs, ...fetchOptions } = options;
  let response;
  try {
    response = await fetch(path, {
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      ...fetchOptions,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("Request timed out. The generation may still be running; check Saved Maps in a minute.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForGenerationJob(jobId) {
  const started = Date.now();
  const maxWaitMs = 12 * 60 * 1000;
  while (Date.now() - started < maxWaitMs) {
    const job = await api(`/api/account-maps/jobs/${jobId}`, { timeoutMs: 30000 });
    if (job.status === "completed") {
      return job.result;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Generation failed");
    }
    const elapsedSeconds = Math.max(1, Math.round((Date.now() - started) / 1000));
    const label = job.status === "queued" ? "Queued" : "Researching";
    setWorking(true, `${label} · ${elapsedSeconds}s`);
    setBusy(
      true,
      job.status === "queued" ? "Queued" : "Researching account",
      job.message || "The account map is still generating. You can leave this page open while the job runs."
    );
    await delay(job.status === "queued" ? 1500 : 3000);
  }
  throw new Error("Generation is still running after 12 minutes. Check Saved Maps shortly, or try again with fewer use cases.");
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => els.toast.classList.remove("show"), 8000);
}

function setWorking(isWorking, label = "Ready") {
  els.generateButton.disabled = isWorking;
  els.generationStatus.textContent = label;
  els.generationStatus.classList.toggle("working", isWorking);
  els.generationStatus.classList.remove("error");
}

function setBusy(isBusy, title = "Working", detail = "Please wait while the workspace updates.") {
  els.loadingTitle.textContent = title;
  els.loadingDetail.textContent = detail;
  els.loadingOverlay.classList.toggle("hidden", !isBusy);
  document.body.classList.toggle("is-busy", isBusy);
  els.newWorkspace.disabled = isBusy;
}

function setPptxWorking(isWorking) {
  els.pptxButton.disabled = isWorking;
  els.pptxButton.textContent = isWorking ? "Exporting" : "Export PPTX";
}

function updateProviderHint() {
  const isOpenAI = els.provider.value === "openai";
  els.apiKey.placeholder = isOpenAI ? "OpenAI key required" : "Anthropic key required";
}

function setError(label) {
  els.generationStatus.textContent = label;
  els.generationStatus.classList.add("error");
  els.generationStatus.classList.remove("working");
}

function uniqueProducts(accountMap) {
  const products = new Set();
  for (const useCase of accountMap.recommended_use_cases || []) {
    for (const product of useCase.opswat_products || []) {
      if (product.slug) products.add(product.slug);
    }
  }
  return products;
}

function renderEmpty(container, label) {
  container.innerHTML = `<div class="empty">${escapeHtml(label)}</div>`;
}

function labelize(value) {
  return String(value || "diagram")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function disableFileLinks() {
  els.jsonLink.href = "#";
  els.mdLink.href = "#";
  els.jsonLink.classList.add("disabled");
  els.mdLink.classList.add("disabled");
  els.pptxButton.classList.add("disabled");
  els.pptxButton.disabled = false;
  els.pptxButton.textContent = "Export PPTX";
}

function clearWorkspace() {
  state.current = null;
  state.currentSummary = null;
  els.target.value = "";
  els.focus.value = "";
  els.provider.value = "anthropic";
  els.apiKey.value = "";
  els.useCases.value = "5";
  els.dryRun.checked = false;
  els.accountName.textContent = "New customer workspace";
  els.accountSector.textContent = "Enter an account and focus area to begin.";
  els.accountSummary.textContent = "";
  els.metricUseCases.textContent = "0";
  els.metricSignals.textContent = "0";
  els.metricSources.textContent = "0";
  els.metricProducts.textContent = "0";
  renderEmpty(els.useCaseList, "Generate an account map to see recommended plays");
  renderEmpty(els.signalList, "No account signals yet");
  renderEmpty(els.buyerList, "No buyer map yet");
  renderEmpty(els.outreachBlock, "No outreach plan yet");
  renderEmpty(els.evidenceList, "No research evidence yet");
  disableFileLinks();
  setWorking(false, "Ready");
  renderSavedMaps(state.summaries);
  els.target.focus();
}

function renderAccountMap(result) {
  const accountMap = result.account_map || result;
  const summary = result.summary || {};
  state.current = accountMap;
  state.currentSummary = summary;

  const target = accountMap.target_account || {};
  const useCases = accountMap.recommended_use_cases || [];
  const signals = accountMap.account_signals || [];
  const evidence = accountMap.research_evidence || [];
  const buyers = accountMap.buyer_map || [];
  const outreach = accountMap.outreach || {};

  els.accountName.textContent = target.name || "Untitled account";
  els.accountSector.textContent = target.sector || "";
  els.accountSummary.textContent = target.summary || "";
  els.metricUseCases.textContent = useCases.length;
  els.metricSignals.textContent = signals.length;
  els.metricSources.textContent = evidence.length;
  els.metricProducts.textContent = uniqueProducts(accountMap).size;

  disableFileLinks();
  if (summary.json_url) {
    els.jsonLink.href = summary.json_url;
    els.jsonLink.classList.remove("disabled");
  }
  if (summary.markdown_url) {
    els.mdLink.href = summary.markdown_url;
    els.mdLink.classList.remove("disabled");
  }
  if (summary.id) {
    els.pptxButton.classList.remove("disabled");
  }

  if (useCases.length) {
    els.useCaseList.innerHTML = useCases
      .map((useCase, index) => {
        const products = (useCase.opswat_products || [])
          .map((product) => `<span class="product-chip">${escapeHtml(product.product || product.slug)}</span>`)
          .join("");
        const questions = (useCase.discovery_questions || [])
          .map((question) => `<li>${escapeHtml(question)}</li>`)
          .join("");
        const diagram = useCase.diagram || {};
        const diagramBlock = diagram.svg_url
          ? `
            <div class="diagram-card">
              <div class="diagram-frame">
                <img src="${escapeHtml(diagram.svg_url)}" alt="${escapeHtml(diagram.title || `${useCase.title} diagram`)}" loading="lazy" />
              </div>
              <div class="diagram-actions">
                <span>${escapeHtml(labelize(diagram.pattern))}</span>
                <a href="${escapeHtml(diagram.svg_url)}" target="_blank" rel="noreferrer">Open SVG</a>
                <a href="${escapeHtml(diagram.json_url || "#")}" target="_blank" rel="noreferrer">Spec</a>
              </div>
            </div>`
          : diagram.error
            ? `<div class="diagram-error">Diagram generation failed: ${escapeHtml(diagram.error)}</div>`
            : "";
        return `
          <article class="use-case">
            <div class="use-case-top">
              <div class="use-case-title"><span class="rank">${index + 1}.</span> ${escapeHtml(useCase.title)}</div>
              <span class="confidence">${escapeHtml(useCase.confidence || "medium")}</span>
            </div>
            <p>${escapeHtml(useCase.account_trigger || useCase.problem)}</p>
            <p>${escapeHtml(useCase.business_value || "")}</p>
            <div class="product-row">${products}</div>
            ${diagramBlock}
            <ul class="question-list">${questions}</ul>
          </article>`;
      })
      .join("");
  } else {
    renderEmpty(els.useCaseList, "No use cases generated");
  }

  if (signals.length) {
    els.signalList.innerHTML = signals
      .map(
        (signal) => `
        <article class="signal">
          <div class="item-title">${escapeHtml(signal.signal)}</div>
          <p>${escapeHtml(signal.why_it_matters)}</p>
        </article>`
      )
      .join("");
  } else {
    renderEmpty(els.signalList, "No account signals");
  }

  if (buyers.length) {
    els.buyerList.innerHTML = buyers
      .map((buyer) => {
        const concerns = (buyer.likely_concerns || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
        return `
          <article class="buyer">
            <div class="item-title">${escapeHtml(buyer.persona)}</div>
            <p>${escapeHtml(buyer.message_angle)}</p>
            <ul class="compact-list">${concerns}</ul>
          </article>`;
      })
      .join("");
  } else {
    renderEmpty(els.buyerList, "No buyer map");
  }

  const subjects = (outreach.email_subjects || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const agenda = (outreach.first_call_agenda || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  els.outreachBlock.innerHTML = `
    <p>${escapeHtml(outreach.opening_angle || "")}</p>
    <ul class="compact-list">${subjects}</ul>
    <ul class="compact-list">${agenda}</ul>
  `;

  if (evidence.length) {
    els.evidenceList.innerHTML = evidence
      .map((item) => {
        const url = item.source_url || "#";
        return `
          <article class="evidence-item">
            <div class="item-title">${escapeHtml(item.claim)}</div>
            <p>
              <a class="source-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">
                ${escapeHtml(item.source_title || url)}
              </a>
              · ${escapeHtml(item.confidence || "")}
            </p>
          </article>`;
      })
      .join("");
  } else {
    renderEmpty(els.evidenceList, "No research evidence");
  }
}

function renderSavedMaps(items) {
  state.summaries = items;
  if (!items.length) {
    renderEmpty(els.savedMaps, "No saved maps");
    return;
  }
  els.savedMaps.innerHTML = items
    .map(
      (item) => `
      <button class="saved-map ${item.id === state.currentSummary?.id ? "active" : ""}" type="button" data-id="${escapeHtml(item.id)}">
        <strong>${escapeHtml(item.target_name)}</strong>
        <span>${escapeHtml(item.use_case_count)} use cases · ${escapeHtml(item.evidence_count)} sources</span>
      </button>`
    )
    .join("");
}

async function loadLibrary(selectLatest = false) {
  const data = await api("/api/account-maps");
  renderSavedMaps(data.items || []);
  if (selectLatest && data.items?.length) {
    await loadMap(data.items[0].id);
  }
}

async function loadMap(id) {
  setBusy(true, "Opening workspace", "Loading saved account map and evidence.");
  try {
    const result = await api(`/api/account-maps/${id}`);
    renderAccountMap(result);
    renderSavedMaps(state.summaries);
  } finally {
    setBusy(false);
  }
}

async function init() {
  try {
    const health = await api("/api/health");
    els.healthText.textContent = "API online";
    els.statusDot.classList.add("ok");
    updateProviderHint();
  } catch (error) {
    els.healthText.textContent = "API offline";
    setError("API error");
    toast(error.message);
  }

  try {
    const capabilities = await api("/api/capabilities");
    state.capabilities = capabilities;
  } catch {
    state.capabilities = null;
  }

  try {
    await loadLibrary(true);
  } catch (error) {
    toast(error.message);
  }
}

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setWorking(true, els.dryRun.checked ? "Building prompt" : "Researching");
  setBusy(
    true,
    els.dryRun.checked ? "Building prompt" : "Researching account",
    els.dryRun.checked
      ? "Preparing the generation request without calling Claude."
      : "Searching public signals, mapping OPSWAT products, and creating use-case diagrams. This can take a little while."
  );
  try {
    const apiKey = els.apiKey.value.trim();
    if (!apiKey) {
      throw new Error(`Enter your ${els.provider.value === "openai" ? "OpenAI" : "Anthropic"} API key before generating.`);
    }
    const generationPayload = {
      target: els.target.value,
      focus: els.focus.value,
      use_cases: Number(els.useCases.value || 5),
      provider: els.provider.value,
      dry_run: els.dryRun.checked,
    };
    if (els.provider.value === "openai") {
      generationPayload.openai_api_key = apiKey;
      generationPayload.model = "gpt-5.5";
      generationPayload.openai_reasoning = "medium";
    } else {
      generationPayload.anthropic_api_key = apiKey;
      generationPayload.model = "claude-opus-4-8";
    }

    const job = await api("/api/account-maps/jobs", {
      method: "POST",
      body: JSON.stringify(generationPayload),
      timeoutMs: 30000,
    });
    setWorking(true, "Queued");
    const result = await waitForGenerationJob(job.id);
    renderAccountMap(result);
    await loadLibrary(false);
    setWorking(false, "Complete");
    toast("Account map generated");
  } catch (error) {
    setError(`Error: ${error.message}`);
    toast(error.message);
  } finally {
    els.generateButton.disabled = false;
    setBusy(false);
  }
});

els.savedMaps.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-id]");
  if (!button) return;
  try {
    await loadMap(button.dataset.id);
    toast("Saved map loaded");
  } catch (error) {
    toast(error.message);
  }
});

els.newWorkspace.addEventListener("click", () => {
  clearWorkspace();
  toast("New workspace ready");
});

els.provider.addEventListener("change", updateProviderHint);

els.pptxButton.addEventListener("click", async () => {
  const id = state.currentSummary?.id;
  if (!id) return;
  setPptxWorking(true);
  setBusy(true, "Building slide deck", "Formatting partner-facing slides from this account map.");
  try {
    const result = await api(`/api/account-maps/${id}/deck`, { method: "POST" });
    state.currentSummary = result.summary || state.currentSummary;
    if (result.deck_url) {
      window.location.href = result.deck_url;
      toast("Slide deck exported");
    }
  } catch (error) {
    toast(error.message);
  } finally {
    setPptxWorking(false);
    setBusy(false);
  }
});

init();

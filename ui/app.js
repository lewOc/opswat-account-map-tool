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
  useCases: document.querySelector("#useCases"),
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
  pdfButton: document.querySelector("#pdfButton"),
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
    const job = await api(`/api/v2/account-maps/jobs/${jobId}`, { timeoutMs: 30000 });
    if (job.status === "completed") {
      return job.result;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Generation failed");
    }
    const elapsedSeconds = Math.max(1, Math.round((Date.now() - started) / 1000));
    const label = job.status === "queued" ? "Queued" : job.stage || "Researching";
    setWorking(true, `${label} · ${elapsedSeconds}s`);
    setBusy(
      true,
      job.status === "queued" ? "Queued" : "Generating v2 account map",
      job.message || "The v2 account map is still generating. You can leave this page open while the job runs."
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

function setPdfWorking(isWorking) {
  els.pdfButton.disabled = isWorking;
  els.pdfButton.textContent = isWorking ? "Exporting" : "Export PDF";
}

function updateProviderHint() {
  if (els.generationStatus.textContent === "Ready") {
    els.generationStatus.textContent = els.provider.value === "openai" ? "Ready · OpenAI" : "Ready · Opus 4.8";
  }
}

async function downloadFile(url, filename) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Download failed: ${response.status}`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
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

function renderList(items, className = "compact-list") {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) return "";
  return `<ul class="${className}">${values.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderUseCaseSection(label, body, variant = "") {
  if (!body) return "";
  return `
    <section class="brief-section ${variant}">
      <div class="brief-label">${escapeHtml(label)}</div>
      <p>${escapeHtml(body)}</p>
    </section>`;
}

function disableFileLinks() {
  els.jsonLink.href = "#";
  els.mdLink.href = "#";
  els.jsonLink.classList.add("disabled");
  els.mdLink.classList.add("disabled");
  els.pdfButton.classList.add("disabled");
  els.pdfButton.disabled = false;
  els.pdfButton.textContent = "Export PDF";
}

function clearWorkspace() {
  state.current = null;
  state.currentSummary = null;
  els.target.value = "";
  els.focus.value = "";
  els.provider.value = "anthropic";
  els.useCases.value = "2";
  updateProviderHint();
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
  if (summary.pdf_url) {
    els.pdfButton.classList.remove("disabled");
  }

  if (useCases.length) {
    els.useCaseList.innerHTML = useCases
      .map((useCase, index) => {
        const products = (useCase.opswat_products || [])
          .map(
            (product) => `
              <article class="product-fit">
                <div class="product-fit-top">
                  <strong>${escapeHtml(product.product || product.slug)}</strong>
                  <span>${escapeHtml(product.confidence || "medium")}</span>
                </div>
                <p>${escapeHtml(product.fit_reason || "")}</p>
                ${renderList(product.capabilities_used || [], "tag-list")}
              </article>`
          )
          .join("");
        const problem = useCase.problem_narrative || useCase.problem || useCase.account_trigger;
        const solution = useCase.solution_narrative || useCase.deployment_hypothesis;
        const value = useCase.business_value_narrative || useCase.business_value;
        const conversationStarter = useCase.conversation_starter;
        const implementationFlow = renderList(useCase.implementation_flow || [], "numbered-list");
        const stakeholders = renderList(useCase.stakeholders || [], "stakeholder-list");
        const questions = renderList(useCase.discovery_questions || [], "question-list");
        const inferences = renderList(useCase.inferences || [], "compact-list inference-list");
        const deliveryExperience = (useCase.delivery_experience || [])
          .map((item) => {
            const products = renderList(item.products || [], "tag-list");
            const source = item.source_url
              ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">Source</a>`
              : "";
            return `
              <article class="delivery-card">
                <div class="delivery-top">
                  <div>
                    <strong>${escapeHtml(item.title || "Relevant OPSWAT delivery example")}</strong>
                    <span>${escapeHtml(item.customer_type || "Similar customer environment")}</span>
                  </div>
                  <div class="delivery-meta">
                    <span>${escapeHtml(item.confidence || "medium")}</span>
                    ${source}
                  </div>
                </div>
                ${products ? `<div class="delivery-products">${products}</div>` : ""}
                ${item.relevance ? `<p>${escapeHtml(item.relevance)}</p>` : ""}
                ${item.outcome ? `<p><b>Outcome:</b> ${escapeHtml(item.outcome)}</p>` : ""}
              </article>`;
          })
          .join("");
        const diagram = useCase.diagram || {};
        const diagramUrl = diagram.image_url || diagram.svg_url;
        const diagramLabel = diagram.image_url ? "Open PNG" : "Open SVG";
        const diagramBlock = diagramUrl
          ? `
            <div class="diagram-card">
              <div class="diagram-frame">
                <img src="${escapeHtml(diagramUrl)}" alt="${escapeHtml(diagram.title || `${useCase.title} diagram`)}" loading="lazy" />
              </div>
              <div class="diagram-actions">
                <span>${escapeHtml(labelize(diagram.pattern))}</span>
                <a href="${escapeHtml(diagramUrl)}" target="_blank" rel="noreferrer">${diagramLabel}</a>
                <a href="${escapeHtml(diagram.json_url || "#")}" target="_blank" rel="noreferrer">Spec</a>
              </div>
            </div>`
          : diagram.error
            ? `<div class="diagram-error">Diagram generation failed: ${escapeHtml(diagram.error)}</div>`
            : "";
        return `
          <article class="use-case">
            <div class="use-case-top">
              <div class="use-case-title"><span class="rank">${index + 1}.</span> ${escapeHtml(useCase.title || "Untitled use case")}</div>
              <span class="confidence">${escapeHtml(useCase.confidence || "medium")}</span>
            </div>
            ${diagramBlock}
            ${renderUseCaseSection("Problem", problem, "problem")}
            ${renderUseCaseSection("Solution", solution, "solution")}
            ${renderUseCaseSection("Business Value", value, "value")}
            ${conversationStarter ? `
              <section class="conversation-card">
                <div class="brief-label">Conversation Starter</div>
                <blockquote>${escapeHtml(conversationStarter)}</blockquote>
              </section>` : ""}
            <div class="brief-grid">
              ${implementationFlow ? `
                <section class="brief-section">
                  <div class="brief-label">Implementation Flow</div>
                  ${implementationFlow}
                </section>` : ""}
              ${stakeholders ? `
                <section class="brief-section">
                  <div class="brief-label">Stakeholders</div>
                  ${stakeholders}
                </section>` : ""}
            </div>
            ${products ? `
              <section class="brief-section">
                <div class="brief-label">Products</div>
                <div class="product-row">${products}</div>
              </section>` : ""}
            ${deliveryExperience ? `
              <section class="brief-section delivery-section">
                <div class="brief-label">Relevant Delivery Experience</div>
                <div class="delivery-list">${deliveryExperience}</div>
              </section>` : ""}
            ${questions ? `
              <section class="brief-section">
                <div class="brief-label">Discovery Questions</div>
                ${questions}
              </section>` : ""}
            ${inferences ? `
              <section class="brief-section">
                <div class="brief-label">Inferences To Validate</div>
                ${inferences}
              </section>` : ""}
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
  const data = await api("/api/v2/account-maps");
  renderSavedMaps(data.items || []);
  if (selectLatest && data.items?.length) {
    await loadMap(data.items[0].id);
  }
}

async function loadMap(id) {
  setBusy(true, "Opening workspace", "Loading saved account map and evidence.");
  try {
    const result = await api(`/api/v2/account-maps/${id}`);
    renderAccountMap(result);
    renderSavedMaps(state.summaries);
  } finally {
    setBusy(false);
  }
}

async function init() {
  try {
    const health = await api("/api/v2/health");
    els.healthText.textContent = health.customer_story_retrieval_configured ? "V2 API online" : "V2 API online · retrieval off";
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
  els.toast.textContent = "";
  els.toast.classList.remove("show");
  setWorking(true, "Researching");
  setBusy(
    true,
    "Generating v2 account map",
    "Searching public signals, retrieving customer-story examples, grounding OPSWAT products, and writing use-case narratives."
  );
  try {
    const generationPayload = {
      target: els.target.value,
      focus: els.focus.value,
      use_cases: Number(els.useCases.value || 5),
      provider: els.provider.value,
    };
    if (els.provider.value === "openai") {
      generationPayload.model = "gpt-5.5";
    } else {
      generationPayload.model = "claude-opus-4-8";
    }

    const job = await api("/api/v2/account-maps/jobs", {
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

els.pdfButton.addEventListener("click", async () => {
  const id = state.currentSummary?.id;
  const pdfUrl = state.currentSummary?.pdf_url || (id ? `/api/v2/account-maps/${id}/pdf` : "");
  if (!id || !pdfUrl) {
    toast("Load or generate an account map before exporting.");
    return;
  }
  setPdfWorking(true);
  setBusy(true, "Building PDF", "Formatting a customer-ready OPSWAT account map.");
  try {
    await downloadFile(pdfUrl, `${id}-opswat-account-map.pdf`);
    toast("PDF exported");
  } catch (error) {
    toast(error.message);
  } finally {
    setPdfWorking(false);
    setBusy(false);
  }
});

init();

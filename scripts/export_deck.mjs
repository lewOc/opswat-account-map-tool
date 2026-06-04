#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const artifactToolModule = process.env.ARTIFACT_TOOL_MODULE || "@oai/artifact-tool/dist/artifact_tool.mjs";
const { FileBlob, PresentationFile } = await import(artifactToolModule);

const WIDTH = 1280;
const HEIGHT = 720;
const FONT = "Simplon Norm";
const COLORS = {
  ink: "#050E22",
  navy: "#0D2553",
  blue: "#2571FB",
  blueSoft: "#EAF2FF",
  cyan: "#03E7F5",
  green: "#00A85A",
  greenSoft: "#E8F8F0",
  red: "#FF003C",
  amber: "#FFB000",
  slate: "#6D7C98",
  line: "#D8E0EC",
  pale: "#F6F8FB",
  white: "#FFFFFF",
};

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) continue;
    args[key.slice(2)] = argv[index + 1];
    index += 1;
  }
  if (!args.input || !args.output || !args.template) {
    throw new Error("Usage: export_deck.mjs --input account-map.json --output deck.pptx --template presentation_template.pptx");
  }
  return args;
}

function valueAt(value, fallback = "") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function truncate(value, max = 120) {
  const text = valueAt(value).replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 3)).trim()}...`;
}

function sentence(value, max = 140) {
  return truncate(valueAt(value).replace(/^[-•]\s*/, ""), max);
}

function shape(slide, x, y, w, h, options = {}) {
  return slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: w, height: h },
    fill: options.fill ?? COLORS.white,
    line: {
      style: "solid",
      fill: options.line ?? COLORS.line,
      width: options.lineWidth ?? 1,
    },
  });
}

function text(slide, value, x, y, w, h, options = {}) {
  const box = shape(slide, x, y, w, h, {
    fill: options.fill ?? "#FFFFFF00",
    line: options.line ?? "#FFFFFF00",
    lineWidth: options.lineWidth ?? 0,
  });
  box.text = valueAt(value);
  box.text.typeface = FONT;
  box.text.fontSize = options.size ?? 16;
  box.text.color = options.color ?? COLORS.ink;
  box.text.bold = Boolean(options.bold);
  box.text.alignment = options.align ?? "left";
  box.text.verticalAlignment = options.valign ?? "top";
  box.text.insets = {
    left: options.insetLeft ?? 0,
    right: options.insetRight ?? 0,
    top: options.insetTop ?? 0,
    bottom: options.insetBottom ?? 0,
  };
  return box;
}

function canvas(slide) {
  shape(slide, 0, 0, WIDTH, HEIGHT, { fill: COLORS.white, line: COLORS.white, lineWidth: 0 });
}

function header(slide, eyebrow, title, accountName) {
  shape(slide, 0, 0, WIDTH, 8, { fill: COLORS.blue, line: COLORS.blue, lineWidth: 0 });
  text(slide, "OPSWAT.", 36, 28, 150, 28, { size: 16, bold: true, color: COLORS.ink });
  text(slide, eyebrow, 36, 84, 230, 22, { size: 12, bold: true, color: COLORS.blue });
  text(slide, title, 36, 104, 820, 62, { size: valueAt(title).length > 66 ? 24 : valueAt(title).length > 54 ? 27 : 32, bold: true, color: COLORS.ink });
  text(slide, truncate(accountName, 92), 920, 38, 310, 54, { size: valueAt(accountName).length > 48 ? 13 : 18, bold: true, color: COLORS.ink, align: "right" });
  shape(slide, 36, 184, 1180, 1, { fill: COLORS.line, line: COLORS.line, lineWidth: 0 });
}

function footer(slide) {
  shape(slide, 0, 682, WIDTH, 38, { fill: COLORS.pale, line: COLORS.pale, lineWidth: 0 });
  text(slide, "OPSWAT.", 36, 695, 120, 18, { size: 12, bold: true, color: COLORS.blue });
  text(slide, "CONFIDENTIAL - ACCOUNT MAP | 2026", 930, 695, 300, 18, { size: 10, color: COLORS.slate, align: "right" });
}

function metric(slide, label, value, x, y, w) {
  const normalized = valueAt(value);
  const valueSize = normalized.length > 8 ? 18 : normalized.length > 4 ? 22 : 28;
  shape(slide, x, y, w, 80, { fill: COLORS.white, line: COLORS.line });
  text(slide, label, x + 18, y + 14, w - 36, 18, { size: 11, bold: true, color: COLORS.slate });
  text(slide, normalized, x + 18, y + 38, w - 36, 24, { size: valueSize, bold: true, color: COLORS.ink });
}

function pill(slide, label, x, y, w, fill = COLORS.blueSoft, color = COLORS.blue) {
  shape(slide, x, y, w, 24, { fill, line: fill, lineWidth: 0 });
  text(slide, truncate(label, Math.max(18, Math.floor(w / 7))), x + 8, y + 5, w - 16, 14, { size: 9, bold: true, color, align: "center" });
}

function compactProductLabel(useCase) {
  const names = productNames(useCase);
  if (!names.length) return "OPSWAT platform";
  const first = truncate(names[0], 42);
  return names.length === 1 ? first : `${first} +${names.length - 1}`;
}

function productRefs(product) {
  return [
    ...(product.evidence_refs || []),
    ...(product.capability_evidence_refs || []),
    ...(product.matched_evidence_refs || []),
  ].filter(Boolean);
}

function productCaps(product) {
  return [
    ...(product.matched_capabilities || []),
    ...(product.capabilities_used || []),
    ...(product.capabilities || []),
  ].filter(Boolean);
}

function bulletList(slide, items, x, y, w, options = {}) {
  const max = options.max ?? items.length;
  items.slice(0, max).forEach((item, index) => {
    const top = y + index * (options.step ?? 54);
    shape(slide, x, top + 4, 8, 8, { fill: options.dot ?? COLORS.blue, line: options.dot ?? COLORS.blue, lineWidth: 0 });
    text(slide, sentence(item.signal || item.title || item.claim || item.persona || item, options.textMax ?? 118), x + 20, top, w - 20, 20, {
      size: options.titleSize ?? 13,
      bold: true,
      color: COLORS.ink,
    });
    const sub = item.why_it_matters || item.message_angle || item.business_value || item.source_title || "";
    if (sub) {
      text(slide, sentence(sub, options.subMax ?? 125), x + 20, top + 22, w - 20, 28, { size: 10, color: COLORS.slate });
    }
  });
}

function textBullets(slide, items, x, y, w, options = {}) {
  const max = options.max ?? items.length;
  items.slice(0, max).forEach((item, index) => {
    const top = y + index * (options.step ?? 34);
    shape(slide, x, top + 5, 6, 6, { fill: options.dot ?? COLORS.blue, line: options.dot ?? COLORS.blue, lineWidth: 0 });
    text(slide, sentence(item, options.textMax ?? 118), x + 16, top, w - 16, options.height ?? 24, {
      size: options.size ?? 10,
      color: options.color ?? COLORS.ink,
    });
  });
}

function productNames(useCase) {
  return (useCase.opswat_products || [])
    .map((product) => product.product || product.slug)
    .filter(Boolean);
}

function productFitText(product, max = 120) {
  return sentence(product.fit_reason || product.product_fit || product.reason || product.description || productCaps(product).join(", "), max);
}

function confidenceColor(confidence) {
  const normalized = valueAt(confidence).toLowerCase();
  if (normalized.includes("high")) return [COLORS.greenSoft, COLORS.green];
  if (normalized.includes("low")) return ["#FFF4E3", COLORS.amber];
  return [COLORS.blueSoft, COLORS.blue];
}

function slideOne(presentation, data) {
  const slide = presentation.slides.add();
  slide.background.fill = COLORS.white;
  canvas(slide);
  const target = data.target_account || {};
  const accountName = target.name || data._meta?.target_input || "Account";
  const signals = data.account_signals || [];
  const evidence = data.research_evidence || [];
  const products = new Set();
  (data.recommended_use_cases || []).forEach((useCase) => productNames(useCase).forEach((product) => products.add(product)));

  header(slide, "ACCOUNT OVERVIEW", accountName, valueAt(target.sector, "Target account"));
  shape(slide, 36, 198, 720, 176, { fill: COLORS.pale, line: COLORS.line });
  text(slide, "Profile", 60, 222, 140, 22, { size: 14, bold: true, color: COLORS.blue });
  text(slide, sentence(target.summary || "Research-backed account map for sales discovery.", 205), 60, 254, 660, 70, { size: 15, color: COLORS.ink });
  text(slide, truncate(valueAt(target.sector, "Sector pending validation"), 120), 60, 342, 660, 18, { size: 10, bold: true, color: COLORS.slate });

  metric(slide, "Use cases", valueAt((data.recommended_use_cases || []).length, "0"), 790, 198, 130);
  metric(slide, "Signals", valueAt(signals.length, "0"), 940, 198, 130);
  metric(slide, "Sources", valueAt(evidence.length, "0"), 1090, 198, 130);
  metric(slide, "Products", valueAt(products.size, "0"), 790, 294, 130);
  shape(slide, 940, 294, 280, 80, { fill: COLORS.blueSoft, line: "#CFE0FF" });
  text(slide, "Partner objective", 962, 312, 220, 18, { size: 11, bold: true, color: COLORS.blue });
  text(slide, "Turn account signals into a focused OPSWAT-led discovery motion.", 962, 337, 230, 28, { size: 11, color: COLORS.ink });

  text(slide, "Highest-value account signals", 36, 418, 360, 24, { size: 16, bold: true, color: COLORS.ink });
  bulletList(slide, signals, 42, 458, 560, { max: 4, step: 48, textMax: 92, subMax: 112 });
  shape(slide, 640, 418, 576, 210, { fill: COLORS.white, line: COLORS.line });
  text(slide, "Sales thesis", 664, 442, 180, 22, { size: 15, bold: true, color: COLORS.blue });
  text(
    slide,
    sentence(
      target.sales_thesis ||
        "Lead with critical infrastructure resilience: secure media, file movement, policy enforcement, and OT protection anchored to OPSWAT capabilities.",
      330
    ),
    664,
    476,
    510,
    86,
    { size: 19, color: COLORS.ink }
  );
  text(slide, "Use this slide to align the partner on why this account is worth a targeted conversation.", 664, 584, 510, 24, {
    size: 11,
    color: COLORS.slate,
  });
  footer(slide);
}

function useCaseSlide(presentation, data, useCase, index) {
  const slide = presentation.slides.add();
  slide.background.fill = COLORS.white;
  canvas(slide);
  const accountName = data.target_account?.name || data._meta?.target_input || "Account";
  const title = useCase.title || useCase.use_case || `Use case ${index + 1}`;
  const products = useCase.opswat_products || [];
  const questions = useCase.discovery_questions || [];
  const [priorityFill, priorityColor] = confidenceColor(useCase.priority || useCase.confidence);
  header(slide, `USE CASE ${index + 1}`, title, accountName);

  pill(slide, valueAt(useCase.priority || useCase.confidence, "priority"), 36, 190, 112, priorityFill, priorityColor);
  pill(slide, compactProductLabel(useCase), 160, 190, 280);

  shape(slide, 36, 230, 560, 152, { fill: COLORS.pale, line: COLORS.line });
  text(slide, "Overview", 60, 252, 160, 20, { size: 14, bold: true, color: COLORS.blue });
  text(slide, sentence(useCase.business_value || useCase.product_fit || useCase.problem, 250), 60, 286, 500, 62, {
    size: 12,
    color: COLORS.ink,
  });
  text(slide, "Partner outcome: qualify where OPSWAT can reduce risk, evidence gaps, or manual control effort.", 60, 358, 500, 14, {
    size: 9,
    color: COLORS.slate,
  });

  shape(slide, 620, 230, 596, 152, { fill: COLORS.white, line: COLORS.line });
  text(slide, "Why this account", 644, 252, 180, 20, { size: 14, bold: true, color: COLORS.blue });
  text(slide, sentence(useCase.account_trigger || useCase.signal_link || useCase.problem, 285), 644, 286, 526, 62, {
    size: 11,
    color: COLORS.ink,
  });
  text(slide, "Use this as the business reason for the partner conversation.", 644, 358, 526, 14, { size: 9, color: COLORS.slate });

  shape(slide, 36, 396, 560, 232, { fill: COLORS.white, line: COLORS.line });
  text(slide, "Products to position", 60, 418, 220, 20, { size: 14, bold: true, color: COLORS.blue });
  const productCardHeight = products.length > 2 ? 50 : 68;
  products.slice(0, 3).forEach((product, productIndex) => {
    const y = 454 + productIndex * (productCardHeight + 8);
    const refCount = productRefs(product).length;
    shape(slide, 60, y, 500, productCardHeight, { fill: productIndex % 2 ? COLORS.white : COLORS.pale, line: COLORS.line });
    text(slide, truncate(product.product || product.slug || "OPSWAT product", 46), 76, y + 8, 250, 16, {
      size: 11,
      bold: true,
      color: COLORS.ink,
    });
    text(slide, `${refCount} OPSWAT doc ref${refCount === 1 ? "" : "s"}`, 392, y + 9, 140, 14, {
      size: 8,
      bold: true,
      color: COLORS.blue,
      align: "right",
    });
    text(slide, sentence(productCaps(product).slice(0, 4).join(" | ") || productFitText(product), 92), 76, y + 27, 450, 12, {
      size: 8,
      color: COLORS.slate,
    });
    if (productCardHeight > 56) {
      text(slide, productFitText(product, 118), 76, y + 43, 450, 14, {
        size: 8,
        color: COLORS.ink,
      });
    }
  });
  if (!products.length) {
    text(slide, "No product mapping available for this use case.", 60, 464, 480, 24, { size: 12, color: COLORS.slate });
  }

  shape(slide, 620, 396, 596, 232, { fill: COLORS.pale, line: COLORS.line });
  text(slide, "Partner discovery path", 644, 418, 240, 20, { size: 14, bold: true, color: COLORS.blue });
  text(slide, "What to validate with the account:", 644, 450, 260, 16, { size: 10, bold: true, color: COLORS.ink });
  textBullets(slide, questions.length ? questions : ["Validate current workflow ownership, constraints, and proof requirements."], 648, 478, 520, {
    max: 4,
    step: 32,
    textMax: 128,
    size: 9,
    dot: COLORS.green,
  });
  text(slide, "Recommended next step", 644, 606, 200, 14, { size: 9, bold: true, color: COLORS.blue });
  text(slide, "Ask the partner to confirm the workflow owner, current control, buying trigger, and proof needed for a technical discovery session.", 796, 606, 370, 14, {
    size: 8,
    color: COLORS.slate,
  });
  footer(slide);
}

function buildProductFit(data) {
  const fit = new Map();
  for (const useCase of data.recommended_use_cases || []) {
    for (const product of useCase.opswat_products || []) {
      const name = product.product || product.slug || "OPSWAT product";
      if (!fit.has(name)) {
        fit.set(name, { name, family: product.family || "", useCases: [], capabilities: [], refs: new Set() });
      }
      const item = fit.get(name);
      item.useCases.push(useCase.title);
      for (const ref of productRefs(product)) {
        item.refs.add(ref);
      }
      for (const capability of productCaps(product)) {
        if (!item.capabilities.includes(capability)) item.capabilities.push(capability);
      }
    }
  }
  return Array.from(fit.values())
    .map((item) => ({ ...item, refCount: item.refs.size }))
    .sort((left, right) => right.useCases.length - left.useCases.length || right.refCount - left.refCount);
}

function slideThree(presentation, data) {
  const slide = presentation.slides.add();
  slide.background.fill = COLORS.white;
  canvas(slide);
  const accountName = data.target_account?.name || data._meta?.target_input || "Account";
  const products = buildProductFit(data).slice(0, 8);
  header(slide, "PRODUCT FIT", "OPSWAT capabilities the partner can position", accountName);

  products.forEach((product, index) => {
    const col = index % 2;
    const row = Math.floor(index / 2);
    const x = col ? 640 : 36;
    const y = 198 + row * 108;
    shape(slide, x, y, 576, 88, { fill: COLORS.white, line: COLORS.line });
    shape(slide, x, y, 6, 88, { fill: col ? COLORS.green : COLORS.blue, line: col ? COLORS.green : COLORS.blue, lineWidth: 0 });
    text(slide, truncate(product.name, 58), x + 22, y + 14, 360, 20, { size: 15, bold: true, color: COLORS.ink });
    const refText = `${product.useCases.length} play${product.useCases.length === 1 ? "" : "s"} | ${product.refCount} doc ref${product.refCount === 1 ? "" : "s"}`;
    text(slide, refText, x + 360, y + 16, 176, 18, {
      size: 10,
      bold: true,
      color: COLORS.blue,
      align: "right",
    });
    text(slide, sentence(product.capabilities.slice(0, 3).join(" | ") || product.useCases.slice(0, 2).join(" | "), 128), x + 22, y + 42, 510, 28, {
      size: 10,
      color: COLORS.slate,
    });
  });

  if (!products.length) {
    shape(slide, 36, 210, 1180, 250, { fill: COLORS.pale, line: COLORS.line });
    text(slide, "No product fit available yet.", 70, 300, 1060, 40, { size: 24, bold: true, color: COLORS.slate, align: "center" });
  }
  footer(slide);
}

function slideFour(presentation, data) {
  const slide = presentation.slides.add();
  slide.background.fill = COLORS.white;
  canvas(slide);
  const accountName = data.target_account?.name || data._meta?.target_input || "Account";
  const buyers = data.buyer_map || [];
  const outreach = data.outreach || {};
  header(slide, "BUYER MAP", "Who to engage and how to open", accountName);

  shape(slide, 36, 198, 560, 404, { fill: COLORS.white, line: COLORS.line });
  text(slide, "Priority personas", 60, 222, 240, 22, { size: 16, bold: true, color: COLORS.blue });
  bulletList(slide, buyers, 64, 266, 490, { max: 5, step: 62, textMax: 62, subMax: 96, dot: COLORS.green });

  shape(slide, 632, 198, 584, 190, { fill: COLORS.pale, line: COLORS.line });
  text(slide, "Opening angle", 656, 222, 180, 22, { size: 16, bold: true, color: COLORS.blue });
  text(slide, sentence(outreach.opening_angle, 300), 656, 258, 510, 74, { size: 18, color: COLORS.ink });

  shape(slide, 632, 412, 280, 190, { fill: COLORS.white, line: COLORS.line });
  text(slide, "Email subjects", 656, 436, 180, 22, { size: 15, bold: true, color: COLORS.ink });
  (outreach.email_subjects || []).slice(0, 4).forEach((item, index) => {
    text(slide, sentence(item, 58), 656, 472 + index * 28, 220, 20, { size: 10, color: COLORS.slate });
  });

  shape(slide, 936, 412, 280, 190, { fill: COLORS.white, line: COLORS.line });
  text(slide, "First call agenda", 960, 436, 180, 22, { size: 15, bold: true, color: COLORS.ink });
  (outreach.first_call_agenda || []).slice(0, 4).forEach((item, index) => {
    text(slide, sentence(item, 58), 960, 472 + index * 28, 220, 20, { size: 10, color: COLORS.slate });
  });
  footer(slide);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const raw = await fs.readFile(args.input, "utf8");
  const data = JSON.parse(raw);
  const presentation = await PresentationFile.importPptx(await FileBlob.load(args.template));

  while (presentation.slides.count > 0) {
    presentation.slides.getItem(0).delete();
  }

  slideOne(presentation, data);
  (data.recommended_use_cases || []).forEach((useCase, index) => {
    useCaseSlide(presentation, data, useCase, index);
  });
  slideFour(presentation, data);

  await fs.mkdir(path.dirname(args.output), { recursive: true });
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(args.output);
  process.stdout.write(args.output);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});

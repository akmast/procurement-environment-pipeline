/*
 * Procurement & Environment Data Pipeline — capstone deck generator.
 * Facts verified against origin/main (commit cf48028, "Remove
 * GoldStandardStateMachine (#27)") — see validation_report.md for the
 * inspection list. Gold Layer has no separate state machine: it always
 * runs inline, inside HistoricalStateMachine/UpdateStateMachine, right
 * after each source's last data stage.
 */
const pptxgen = require("pptxgenjs");

// ---------------------------------------------------------------------
// Palette (spec-provided, hex without '#')
// ---------------------------------------------------------------------
const C = {
  bg: "F7F4EC",
  text: "26352F",
  border: "315B4C",
  eea: "CFE8FF",
  ted: "FFF0B8",
  eurostat: "D7F4DE",
  aws: "FFD9B8",
  state: "DDD8FF",
  gold: "F5D46F",
  success: "BFE6C8",
  failure: "F5C2C0",
  meta: "E8E5DF",
  future: "D9C7F2",
  white: "FFFFFF",
};

const ACCENT = {
  1: "D96C4F", 2: "D96C4F",
  3: "7EA66A",
  4: "4F9B8B", 5: "4F9B8B",
  6: "5E86B3", 7: "5E86B3",
  8: "6F72B8", 9: "6F72B8",
  10: "8067A8", 11: "8067A8", 12: "8067A8",
  13: "9969A8", 14: "9969A8",
};

const SANS = "Calibri";
const MONO = "Courier New";

const PAGE_W = 13.333;
const PAGE_H = 7.5;
const MARGIN = 0.4;

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------
function newSlide(pres, n) {
  const slide = pres.addSlide();
  slide.background = { color: C.bg };
  slide.addText(String(n).padStart(2, "0"), {
    x: 12.55, y: 7.08, w: 0.55, h: 0.3, isTextBox: true,
    fontFace: MONO, fontSize: 11, color: ACCENT[n], align: "right",
    bold: true,
  });
  slide.addText("PROCUREMENT & ENVIRONMENT PIPELINE", {
    x: 0.4, y: 7.08, w: 5.5, h: 0.3, isTextBox: true,
    fontFace: SANS, fontSize: 9, color: "6B7A72", align: "left",
    charSpacing: 1,
  });
  return slide;
}

function title(slide, n, text, opts) {
  const o = Object.assign({
    x: 0.4, y: 0.38, w: 12.5, h: 0.95, fontSize: 36,
  }, opts || {});
  slide.addText(text, {
    x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true,
    fontFace: SANS, fontSize: o.fontSize, color: C.text, bold: true,
    align: o.align || "left", valign: "top", margin: 0,
    lineSpacingMultiple: 1.0,
  });
}

function eyebrow(slide, text, opts) {
  const o = Object.assign({ x: 0.4, y: 0.12, w: 6, h: 0.3 }, opts || {});
  slide.addText(text.toUpperCase(), {
    x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true,
    fontFace: SANS, fontSize: 16, color: o.color || C.border, bold: true,
    charSpacing: 2, margin: 0,
  });
}

function card(slide, opts) {
  const o = Object.assign({ fill: C.white, line: C.border, lineW: 1, radius: 0.1 }, opts);
  const lineOpt = o.lineW ? { color: o.line, width: o.lineW } : { type: "none" };
  return slide.addShape("roundRect", {
    x: o.x, y: o.y, w: o.w, h: o.h,
    fill: { color: o.fill },
    line: lineOpt,
    rectRadius: o.radius,
    shadow: o.shadow,
  });
}

function pill(slide, text, x, y, w, h, fill, opts) {
  const o = opts || {};
  slide.addShape("roundRect", {
    x, y, w, h, fill: { color: fill }, line: { type: "none" }, rectRadius: h / 2,
  });
  slide.addText(text, {
    x: x + 0.05, y, w: w - 0.1, h, isTextBox: true,
    fontFace: SANS, fontSize: o.fontSize || 16, color: o.color || C.text,
    bold: o.bold !== false, align: "center", valign: "middle", margin: 0,
  });
}

function bodyText(slide, text, x, y, w, h, opts) {
  const o = opts || {};
  slide.addText(text, {
    x, y, w, h, isTextBox: true,
    fontFace: o.fontFace || SANS, fontSize: o.fontSize || 16,
    color: o.color || C.text, bold: !!o.bold, italic: !!o.italic,
    align: o.align || "left", valign: o.valign || "top", margin: 0,
    lineSpacingMultiple: o.lineSpacingMultiple || 1.05,
    bullet: o.bullet || false,
  });
}

function bulletList(slide, items, x, y, w, h, opts) {
  const o = opts || {};
  const paras = items.map((t, i) => ({
    text: t,
    options: {
      bullet: { code: "2022", indent: 14 },
      breakLine: i < items.length - 1,
      color: o.color || C.text, fontSize: o.fontSize || 16,
      fontFace: o.fontFace || SANS, bold: !!o.bold,
      paraSpaceAfter: o.paraSpaceAfter != null ? o.paraSpaceAfter : 6,
    },
  }));
  slide.addText(paras, { x, y, w, h, isTextBox: true, valign: "top", margin: 0 });
}

function codeBlock(slide, lines, x, y, w, h, opts) {
  const o = opts || {};
  slide.addShape("roundRect", {
    x, y, w, h, fill: { color: o.fill || "2F3A35" },
    line: { type: "none" }, rectRadius: 0.06,
  });
  const paras = lines.map((t, i) => ({
    text: t,
    options: { breakLine: i < lines.length - 1, color: o.color || "EDEAE0" },
  }));
  slide.addText(paras, {
    x: x + 0.14, y: y + 0.08, w: w - 0.28, h: h - 0.16, isTextBox: true,
    fontFace: MONO, fontSize: o.fontSize || 16, valign: "top", margin: 0,
    lineSpacingMultiple: 1.02,
  });
}

function vArrow(slide, x, y1, y2, opts) {
  const o = opts || {};
  slide.addShape("line", {
    x, y: y1, w: 0, h: y2 - y1,
    line: {
      color: o.color || C.border, width: o.width || 1.5,
      dashType: o.dashed ? "dash" : "solid",
      endArrowType: "triangle",
    },
  });
  if (o.label) {
    slide.addText(o.label, {
      x: x + 0.12, y: (y1 + y2) / 2 - 0.13, w: o.labelW || 2.2, h: 0.26,
      isTextBox: true, fontFace: MONO, fontSize: 16, color: o.color || C.border,
      align: "left", valign: "middle", margin: 0,
    });
  }
}

function hArrow(slide, x1, x2, y, opts) {
  const o = opts || {};
  slide.addShape("line", {
    x: x1, y, w: x2 - x1, h: 0,
    line: {
      color: o.color || C.border, width: o.width || 1.5,
      dashType: o.dashed ? "dash" : "solid",
      endArrowType: "triangle",
    },
  });
}

function shapeNode(slide, type, text, x, y, w, h, fill, opts) {
  const o = opts || {};
  slide.addShape(type, {
    x, y, w, h, fill: { color: fill },
    line: { color: o.line || C.border, width: o.lineW || 1 },
    rectRadius: o.radius,
  });
  if (text) {
    slide.addText(text, {
      x: x + 0.05, y, w: w - 0.1, h, isTextBox: true,
      fontFace: SANS, fontSize: o.fontSize || 16, color: o.color || C.text,
      bold: o.bold !== false, align: "center", valign: "middle", margin: 0,
      lineSpacingMultiple: 0.95,
    });
  }
}

function statTile(slide, value, label, x, y, w, h, accent) {
  card(slide, { x, y, w, h, fill: C.white, line: "D8D3C6" });
  slide.addText(value, {
    x: x + 0.1, y: y + 0.08, w: w - 0.2, h: h * 0.58, isTextBox: true,
    fontFace: SANS, fontSize: 30, bold: true, color: accent || C.border,
    align: "center", valign: "bottom", margin: 0,
  });
  slide.addText(label, {
    x: x + 0.1, y: y + h * 0.6, w: w - 0.2, h: h * 0.36, isTextBox: true,
    fontFace: SANS, fontSize: 16, color: C.text, align: "center", valign: "top", margin: 0,
  });
}

function tableBlock(slide, headers, rows, x, y, w, opts) {
  const o = opts || {};
  const rowH = o.rowH || 0.34;
  const cols = headers.map((h, i) => ({
    text: h, options: {
      bold: true, fill: { color: o.headerFill || C.meta }, color: C.text,
      fontFace: SANS, fontSize: o.fontSize || 16, align: o.align ? o.align[i] : "left",
      valign: "middle", border: { type: "solid", color: "D8D3C6", pt: 0.75 },
    },
  }));
  const body = rows.map((r) => r.map((cell, i) => ({
    text: String(cell), options: {
      color: C.text, fontFace: o.mono && i === -1 ? MONO : SANS,
      fontSize: o.fontSize || 16, align: o.align ? o.align[i] : "left",
      valign: "middle", border: { type: "solid", color: "E3DFD3", pt: 0.5 },
      fill: { color: C.white },
    },
  })));
  slide.addTable([cols, ...body], {
    x, y, w, colW: o.colW,
    rowH: [rowH * 1.15, ...rows.map(() => rowH)],
    autoPage: false,
  });
}

function sourceBadge(slide, label, x, y, w, h, fill) {
  slide.addShape("roundRect", {
    x, y, w, h, fill: { color: fill }, line: { type: "none" }, rectRadius: 0.08,
  });
  slide.addText(label, {
    x, y, w, h, isTextBox: true, fontFace: SANS, fontSize: 16, bold: true,
    color: C.text, align: "center", valign: "middle", margin: 0,
  });
}

// ---------------------------------------------------------------------
// Build
// ---------------------------------------------------------------------
const pres = new pptxgen();
pres.defineLayout({ name: "WIDE", width: PAGE_W, height: PAGE_H });
pres.layout = "WIDE";
pres.author = "Procurement & Environment Data Pipeline";
pres.title = "Procurement & Environment Data Pipeline";

require("./slides/slide01")(pres, { newSlide, title, eyebrow, card, pill, bodyText, bulletList, C, ACCENT, SANS, MONO });
require("./slides/slide02")(pres, { newSlide, title, eyebrow, card, pill, bodyText, bulletList, C, ACCENT, SANS, MONO });
require("./slides/slide03")(pres, { newSlide, title, eyebrow, card, pill, bodyText, bulletList, codeBlock, tableBlock, sourceBadge, C, ACCENT, SANS, MONO });
require("./slides/slide04")(pres, { newSlide, title, eyebrow, card, bodyText, vArrow, shapeNode, C, ACCENT, SANS, MONO });
require("./slides/slide05")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, vArrow, shapeNode, C, ACCENT, SANS, MONO });
require("./slides/slide06")(pres, { newSlide, title, eyebrow, card, bodyText, vArrow, shapeNode, codeBlock, C, ACCENT, SANS, MONO });
require("./slides/slide07")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, vArrow, hArrow, shapeNode, codeBlock, tableBlock, C, ACCENT, SANS, MONO });
require("./slides/slide08")(pres, { newSlide, title, eyebrow, card, bodyText, vArrow, shapeNode, sourceBadge, C, ACCENT, SANS, MONO });
require("./slides/slide09")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, vArrow, shapeNode, codeBlock, sourceBadge, C, ACCENT, SANS, MONO });
require("./slides/slide10")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, vArrow, hArrow, shapeNode, codeBlock, sourceBadge, C, ACCENT, SANS, MONO });
require("./slides/slide11")(pres, { newSlide, title, eyebrow, card, bodyText, vArrow, shapeNode, sourceBadge, C, ACCENT, SANS, MONO });
require("./slides/slide12")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, vArrow, shapeNode, C, ACCENT, SANS, MONO });
require("./slides/slide13")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, statTile, tableBlock, C, ACCENT, SANS, MONO });
require("./slides/slide14")(pres, { newSlide, title, eyebrow, card, bodyText, bulletList, C, ACCENT, SANS, MONO });

pres.writeFile({ fileName: "Procurement_Environment_Data_Pipeline.pptx" })
  .then(() => console.log("Written Procurement_Environment_Data_Pipeline.pptx"))
  .catch((err) => { console.error(err); process.exit(1); });

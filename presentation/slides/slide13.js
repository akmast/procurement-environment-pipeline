module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, bulletList, statTile, tableBlock, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 13);
  const accent = ACCENT[13];

  title(slide, 13, "Current Data Snapshot", { fontSize: 35 });

  // ---- Stat tiles ---------------------------------------------------------
  const tileY = 1.32, tileH = 0.62, tileW = 2.97, tileGap = 0.17;
  const stats = [
    { v: "54,099", l: "EEA rows", c: "1D5F8A" },
    { v: "61,794", l: "TED notices", c: "8A6D1D" },
    { v: "27,585", l: "Eurostat rows", c: "2E7A4A" },
    { v: "0", l: "duplicate rows", c: C.text },
  ];
  stats.forEach((s, i) => {
    const x = 0.4 + i * (tileW + tileGap);
    card(slide, { x, y: tileY, w: tileW, h: tileH, fill: C.white, line: "D8D3C6", lineW: 1, radius: 0.1 });
    slide.addText(s.v, {
      x: x + 0.14, y: tileY, w: tileW * 0.5, h: tileH, isTextBox: true,
      fontFace: SANS, fontSize: 26, bold: true, color: s.c, valign: "middle", margin: 0,
    });
    slide.addText(s.l, {
      x: x + tileW * 0.52, y: tileY, w: tileW * 0.46, h: tileH, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, valign: "middle", margin: 0, lineSpacingMultiple: 1.0,
    });
  });

  // ---- Coverage table (left) ----------------------------------------------
  const covY = tileY + tileH + 0.2;
  slide.addText("COVERAGE", {
    x: 0.4, y: covY, w: 6.1, h: 0.24, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: accent, margin: 0,
  });
  tableBlock(slide,
    ["Dataset", "Ctry", "Rows", "Yrs", "Available years"],
    [
      ["EEA meas.", "DE", "53,796", "2", "2025, 2026"],
      ["EEA meas.", "PL", "303", "1", "2025"],
      ["TED notices", "DE", "24,154", "6", "2021–2026"],
      ["TED notices", "PL", "37,640", "6", "2021–2026"],
      ["Eurostat agri", "DE", "19,904", "3", "2021–2023"],
      ["Eurostat agri", "PL", "7,681", "2", "2021, 2023"],
    ], 0.4, covY + 0.28, 6.1, { fontSize: 16, rowH: 0.3, colW: [1.7, 0.6, 1.0, 0.6, 2.2], align: ["left", "center", "right", "center", "left"] });

  // ---- Quality table (right, top) -----------------------------------------
  const rx = 6.75, rw = 6.15;
  slide.addText("QUALITY", {
    x: rx, y: covY, w: rw, h: 0.24, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: accent, margin: 0,
  });
  tableBlock(slide,
    ["Dataset", "Rows", "Cols", "Dup rows", "Rows w/ null"],
    [
      ["EEA meas.", "54,099", "14", "0", "131"],
      ["TED notices", "61,794", "13", "0", "41,231"],
      ["Eurostat agri", "27,585", "12", "0", "0"],
    ], rx, covY + 0.28, rw, { fontSize: 16, rowH: 0.3, colW: [2.15, 1.1, 0.75, 1.1, 1.05], align: ["left", "right", "right", "right", "right"] });

  // ---- Domain callouts (right, below quality table) -----------------------
  const qualityTableH = 0.3 * 1.15 + 3 * 0.3;
  const domY = covY + 0.28 + qualityTableH + 0.5;
  const domains = [
    { t: "EEA Gold", l: "PM10 + PM2.5 · valid 34,159 / 18,399", fill: C.eea },
    { t: "TED currencies", l: "PLN 29,832 · EUR 20,462 · Missing 8,944", fill: C.ted },
    { t: "Eurostat indicators", l: "Production value · Subsidies · Taxes", fill: C.eurostat },
  ];
  let dy = domY;
  const domH = 0.46;
  domains.forEach((d) => {
    card(slide, { x: rx, y: dy, w: rw, h: domH, fill: d.fill, line: "00000000", lineW: 0, radius: 0.08 });
    slide.addText([
      { text: d.t, options: { bold: true, breakLine: true } },
      { text: d.l, options: {} },
    ], {
      x: rx + 0.14, y: dy + 0.02, w: rw - 0.28, h: domH - 0.04, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, valign: "middle", margin: 0, lineSpacingMultiple: 1.0,
    });
    dy += domH + 0.08;
  });

  // ---- Limitations ----------------------------------------------------------
  const limY = dy + 0.1;
  const limits = ["PL EEA coverage is limited", "PL Eurostat 2022 is missing", "TED value fields are often nullable", "Pattern comparison, not causation"];
  limits.forEach((l, i) => {
    slide.addText("•  " + l, {
      x: rx, y: limY + i * 0.26, w: rw, h: 0.24, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, margin: 0,
    });
  });

  slide.addNotes(
    "These are the real current numbers in this dataset, not projections. 54,099 EEA measurement rows, 61,794 " +
    "TED notices, 27,585 Eurostat rows, and zero duplicate rows across all three Gold tables — that's the " +
    "exact-duplicate check working as designed. Coverage is genuinely uneven: Poland's EEA coverage is much " +
    "thinner than Germany's, since fewer stations are currently ingested there; Eurostat is missing 2022 for " +
    "Poland specifically, not for Germany. TED's 41,231 rows with a null somewhere is expected, not a pipeline " +
    "failure — those are almost entirely the contract_total_value/contract_currency_code fields, and TED " +
    "notices genuinely omit contract value information often (about a third of TED rows have a missing " +
    "currency, PLN and EUR dominate what is filled in). The EEA Gold table currently holds only PM10 and PM2.5, " +
    "not NO2/O3/SO2 yet, even though those pollutants exist in the source API — that's a scope decision for " +
    "this MVP, not a bug. And to repeat the caveat from earlier: everything here supports pattern comparison " +
    "across regions, never a causal claim about what drove what."
  );
};

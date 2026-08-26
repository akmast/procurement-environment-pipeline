module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, shapeNode, sourceBadge, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 8);
  const accent = ACCENT[8];

  title(slide, 8, "Historical Loads Three Sources in Parallel", { fontSize: 35 });

  // ---- Controller band --------------------------------------------------
  const ctrlY = 1.28, ctrlH = 0.42;
  card(slide, { x: 0.4, y: ctrlY, w: 12.5, h: ctrlH, fill: C.meta, line: "D8D3C6", lineW: 0.75, radius: 0.08 });
  slide.addText([
    { text: "Manual start (Actions → Run Pipeline)   ", options: { bold: true } },
    { text: "sources=eea,ted,eurostat  countries=DE,PL  from_year=2021  to_year=2025   ", options: { fontFace: MONO, color: C.border } },
    { text: "→ CheckBootstrapComplete → run_id → RunSources", options: { bold: true } },
  ], {
    x: 0.6, y: ctrlY, w: 12.1, h: ctrlH, isTextBox: true,
    fontFace: SANS, fontSize: 16, color: C.text, valign: "middle", margin: 0,
  });

  const laneW = 4.0, gap = 0.25;
  const lanesX = [0.4, 0.4 + laneW + gap, 0.4 + 2 * (laneW + gap)];
  const laneTop = ctrlY + ctrlH + 0.14;

  function laneHeader(x, name, module_, fill) {
    card(slide, { x, y: laneTop, w: laneW, h: 0.36, fill, line: "00000000", lineW: 0, radius: 0.08 });
    slide.addText(name, {
      x: x + 0.14, y: laneTop, w: laneW * 0.34, h: 0.36, isTextBox: true,
      fontFace: SANS, fontSize: 17, bold: true, color: C.text, valign: "middle", margin: 0,
    });
    slide.addText(module_, {
      x: x + laneW * 0.3, y: laneTop, w: laneW * 0.68, h: 0.36, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "3F4E47", valign: "middle", align: "right", margin: 0,
    });
    return laneTop + 0.36 + 0.08;
  }

  function stage(x, y, header, writeLine, manifestLine, fillHeader) {
    slide.addText(header, {
      x, y, w: laneW, h: 0.22, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: fillHeader || accent, margin: 0,
    });
    const by = y + 0.24;
    slide.addShape("roundRect", { x, y: by, w: laneW, h: 0.62, fill: { color: "2F3A35" }, line: { type: "none" }, rectRadius: 0.06 });
    slide.addText([
      { text: "WRITE " + writeLine, options: { breakLine: true } },
      { text: "MANIFEST " + manifestLine, options: {} },
    ], {
      x: x + 0.12, y: by + 0.04, w: laneW - 0.24, h: 0.5, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "EDEAE0", valign: "top", margin: 0, lineSpacingMultiple: 1.0,
    });
    return 0.24 + 0.62 + 0.06;
  }

  function noTransform(x, y) {
    slide.addShape("roundRect", {
      x, y, w: laneW, h: 0.36, fill: { color: C.bg }, line: { color: C.border, width: 1, dashType: "dash" }, rectRadius: 0.08,
    });
    slide.addText("NO TRANSFORMATION STAGE", {
      x, y, w: laneW, h: 0.36, isTextBox: true, fontFace: SANS, fontSize: 16, italic: true,
      color: "56655D", align: "center", valign: "middle", margin: 0,
    });
    return 0.36 + 0.06;
  }

  function sample(x, y, text) {
    slide.addText(text, {
      x, y, w: laneW, h: 0.24, isTextBox: true, fontFace: MONO, fontSize: 16, color: C.text, margin: 0,
    });
  }

  // EEA
  let ey = laneHeader(lanesX[0], "EEA", "measurements.py", C.eea);
  ey += stage(lanesX[0], ey, "INGESTION", "raw/*.parquet", "ingestion.json");
  ey += stage(lanesX[0], ey, "NORMALIZATION", "normalized/*.parquet", "normalization.json");
  ey += stage(lanesX[0], ey, "TRANSFORMATION — +NUTS1–3", "transformed/*.parquet", "transformation.json");
  ey += stage(lanesX[0], ey, "GOLD  --discover", "measurements.parquet", "gold.json", C.gold);
  sample(lanesX[0], ey + 0.02, "DEBE034·PM10·18.4 → Berlin·DE300");

  // TED
  let ty = laneHeader(lanesX[1], "TED", "notices.py", C.ted);
  ty += stage(lanesX[1], ty, "INGESTION — pagination", "notices.jsonl", "ingestion.json");
  ty += stage(lanesX[1], ty, "NORMALIZATION", "notices.parquet", "normalization.json");
  ty += stage(lanesX[1], ty, "TRANSFORMATION — +labels", "notices.parquet", "transformation.json");
  ty += stage(lanesX[1], ty, "GOLD  --discover", "notices.parquet", "gold.json", C.gold);
  sample(lanesX[1], ty + 0.02, "123456-2025 → Berlin·Euro");

  // Eurostat
  let uy = laneHeader(lanesX[2], "Eurostat", "agri_accounts.py", C.eurostat);
  uy += stage(lanesX[2], uy, "INGESTION", "aact_eaa01_r.json", "ingestion.json");
  uy += stage(lanesX[2], uy, "NORMALIZATION — cube→rows", "aact_eaa01_r.parquet", "normalization.json");
  uy += noTransform(lanesX[2], uy);
  uy += stage(lanesX[2], uy, "GOLD  --discover", "agri_accounts.parquet", "gold.json", C.gold);
  sample(lanesX[2], uy + 0.02, "273.94 → DE11·2023·Cereals");

  // ---- Merge -------------------------------------------------------------
  const mergeY = 6.95 - 0.4;
  card(slide, { x: 0.4, y: mergeY, w: 8.3, h: 0.4, fill: C.state, line: "00000000", lineW: 0, radius: 0.08 });
  slide.addText("EvaluateOverallStatus  →  HistoricalSucceeded | HistoricalFailed", {
    x: 0.6, y: mergeY, w: 7.9, h: 0.4, isTextBox: true,
    fontFace: MONO, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
  });
  slide.addText("run_id links tasks · manifests · logs · resume", {
    x: 8.9, y: mergeY, w: 4.0, h: 0.4, isTextBox: true,
    fontFace: SANS, fontSize: 16, italic: true, color: "56655D", valign: "middle", margin: 0,
  });

  slide.addNotes(
    "HistoricalStateMachine is the manual backfill controller. Its input names which source families to run, " +
    "the country list, and the year range; it first confirms bootstrap is COMPLETE, then either generates a " +
    "fresh run_id or reuses one supplied for a resume. The three source branches run fully in parallel and are " +
    "independent — one failing doesn't stop the others. Each branch's stages are exactly the sequence from the " +
    "architecture slide: EEA and TED both run ingestion, normalization, transformation, then Gold; Eurostat " +
    "skips transformation entirely and goes straight from normalization to Gold, since its normalized rows are " +
    "already analysis-ready — that's a real, current property of the code, not a placeholder. Gold at the end " +
    "of each branch is invoked with --discover, meaning it rebuilds that source's whole Gold table from every " +
    "currently normalized/transformed file — this is the historical backfill path, so a full rebuild is what's " +
    "wanted. Every stage writes its own manifest to S3 keyed by run_id, source, and stage name; that manifest " +
    "is exactly what makes the resume feature on the next slide's operational sibling possible. The branch " +
    "merges into EvaluateOverallStatus, which fails the whole run if any one branch failed."
  );
};

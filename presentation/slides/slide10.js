module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, hArrow, shapeNode, codeBlock, sourceBadge, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 10);
  const accent = ACCENT[10];

  title(slide, 10, "Gold: One Analytical Table per Source", { fontSize: 35 });

  // ---- Left: architecture flow -------------------------------------------
  const lx = 0.4, lw = 6.0;
  let y = 1.4;

  const sources = [
    { t: "EEA transformed", fill: C.eea },
    { t: "TED transformed", fill: C.ted },
    { t: "Eurostat normalized", fill: C.eurostat },
  ];
  const srcH = 0.44, srcGap = 0.12;
  sources.forEach((s, i) => {
    shapeNode(slide, "roundRect", null, lx, y + i * (srcH + srcGap), 2.7, srcH, s.fill, { radius: 0.08, line: C.border, lineW: 0.75 });
    slide.addText(s.t, {
      x: lx + 0.14, y: y + i * (srcH + srcGap), w: 2.42, h: srcH, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
    });
    hArrow(slide, lx + 2.7, lx + 3.35, y + i * (srcH + srcGap) + srcH / 2, {});
  });
  const midY = y + srcH + srcGap + srcH / 2;
  shapeNode(slide, "roundRect", null, lx + 3.35, midY - 0.35, 2.65, 0.7, C.gold, { radius: 0.08, line: C.border, lineW: 0.75 });
  slide.addText("Gold builders\n(gold/<source>/*.py)", {
    x: lx + 3.49, y: midY - 0.35, w: 2.37, h: 0.7, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", align: "center", margin: 0, lineSpacingMultiple: 1.0,
  });

  const s3Y = y + 3 * (srcH + srcGap) + 0.35;
  vArrow(slide, lx + 3.35 + 1.325, midY + 0.35, s3Y, {});
  shapeNode(slide, "ellipse", null, lx + 2.05, s3Y, 2.6, 0.5, C.meta, { line: C.border, lineW: 1 });
  slide.addText("S3 Gold Parquet", {
    x: lx + 2.05, y: s3Y, w: 2.6, h: 0.5, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, align: "center", valign: "middle", margin: 0,
  });

  const pathsY = s3Y + 0.7;
  const paths = [
    "data/gold/eea/measurements.parquet",
    "data/gold/ted/notices.parquet",
    "data/gold/eurostat/",
    "  agriculture_accounts.parquet",
  ];
  const pathsH = paths.length * 0.27 + 0.2;
  codeBlock(slide, paths, lx, pathsY, lw, pathsH, { fontSize: 16 });

  const princY = pathsY + pathsH + 0.16;
  slide.addText("GOLD PRINCIPLES", {
    x: lx, y: princY, w: lw, h: 0.22, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: accent, margin: 0,
  });
  const princ = ["One schema per source", "Required columns only", "Stable names and data types", "Exact duplicate removal", "Dashboard-ready Parquet"];
  princ.forEach((p, i) => {
    slide.addText("•  " + p, {
      x: lx, y: princY + 0.24 + i * 0.23, w: lw, h: 0.22, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, margin: 0,
    });
  });

  // ---- Right: schema samples -----------------------------------------
  const rx = 6.75, rw = 6.15;
  const schemas = [
    { name: "EEA", fill: C.eea, cols: "country_code · sampling_point_id\npollutant_code · measurement_value\nperiod_start · nuts1 · nuts2 · nuts3" },
    { name: "TED", fill: C.ted, cols: "notice_publication_number\nnotice_publication_date · nuts2\ncontract_total_value\ncontract_currency_code" },
    { name: "Eurostat", fill: C.eurostat, cols: "country_code · nuts2\nagricultural_item_label\nagricultural_indicator_label\nreference_year · indicator_value" },
  ];
  let ry = 1.4;
  schemas.forEach((s) => {
    shapeNode(slide, "roundRect", null, rx, ry, rw, 0.34, s.fill, { radius: 0.08 });
    slide.addText(s.name, {
      x: rx + 0.14, y: ry, w: rw - 0.28, h: 0.34, isTextBox: true,
      fontFace: SANS, fontSize: 17, bold: true, color: C.text, valign: "middle", margin: 0,
    });
    ry += 0.38;
    const lines = s.cols.split("\n");
    const h = lines.length * 0.26 + 0.14;
    codeBlock(slide, lines, rx, ry, rw, h, { fontSize: 16 });
    ry += h + 0.24;
  });

  card(slide, { x: rx, y: ry, w: rw, h: 0.5, fill: accent, line: "00000000", lineW: 0, radius: 0.1 });
  slide.addText("Gold runs inside Historical and Update", {
    x: rx + 0.2, y: ry, w: rw - 0.4, h: 0.5, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.white, valign: "middle", margin: 0,
  });

  slide.addNotes(
    "Gold is the layer the dashboard actually touches — one fixed, documented schema per source, always in " +
    "S3 as Parquet. Each source's own Gold builder reads its own precursor: EEA and TED read their Transformed " +
    "output, Eurostat reads Normalized directly since it has no Transformed layer. Every Gold table is one " +
    "schema, required columns only, stable names and types, with exact duplicate rows removed — that's what " +
    "makes it dashboard-ready rather than just 'the latest processed data.' The columns shown here are the real " +
    "columns from the current Gold tables, not illustrative examples — verified against the Terraform Glue " +
    "table definitions. One thing worth saying explicitly, since it surprises people who've seen other versions " +
    "of this architecture: there is no separate Gold state machine. Gold always runs as the last automatic step " +
    "inside HistoricalStateMachine and UpdateStateMachine, right after each source's last data stage — never a " +
    "separate orchestration path."
  );
};

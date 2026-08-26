module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, hArrow, shapeNode, codeBlock, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 7);
  const accent = ACCENT[7];

  title(slide, 7, "Bootstrap Prepares Shared Reference Data", { fontSize: 35 });

  // ---- Controller strip ---------------------------------------------
  const ctrlY = 1.22, ctrlH = 0.5;
  const ctrlSteps = ["Manual start\ncountries_csv = \"DE,PL\"", "Generate\nrun_id", "RunReferencePipelines\n3 parallel branches"];
  const ctrlW = [3.9, 2.6, 4.7], ctrlGap = 0.2;
  let cx = 0.4;
  ctrlSteps.forEach((t, i) => {
    shapeNode(slide, "roundRect", null, cx, ctrlY, ctrlW[i], ctrlH, i === 2 ? C.state : C.white, { radius: 0.08, line: C.border, lineW: 0.75 });
    const lines = t.split("\n");
    const paras = lines.map((ln, j) => ({
      text: ln, options: { breakLine: j < lines.length - 1, bold: j === 0, fontSize: 16, color: C.text },
    }));
    slide.addText(paras, {
      x: cx + 0.1, y: ctrlY, w: ctrlW[i] - 0.2, h: ctrlH, isTextBox: true,
      fontFace: SANS, valign: "middle", align: "center", margin: 0, lineSpacingMultiple: 1.0,
    });
    if (i < ctrlSteps.length - 1) {
      hArrow(slide, cx + ctrlW[i], cx + ctrlW[i] + ctrlGap, ctrlY + ctrlH / 2, {});
    }
    cx += ctrlW[i] + ctrlGap;
  });

  const laneW = 4.0, gap = 0.25;
  const lanesX = [0.4, 0.4 + laneW + gap, 0.4 + 2 * (laneW + gap)];
  const laneTop = ctrlY + ctrlH + 0.14;

  function laneHeader(x, name, module_, fill) {
    card(slide, { x, y: laneTop, w: laneW, h: 0.38, fill, line: "00000000", lineW: 0, radius: 0.09 });
    slide.addText(name, {
      x: x + 0.14, y: laneTop, w: laneW * 0.55, h: 0.38, isTextBox: true,
      fontFace: SANS, fontSize: 17, bold: true, color: C.text, valign: "middle", margin: 0,
    });
    slide.addText(module_, {
      x: x + laneW * 0.3, y: laneTop, w: laneW * 0.68, h: 0.38, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "3F4E47", valign: "middle", align: "right", margin: 0,
    });
  }

  function stageCard(x, y, header, lines) {
    slide.addText(header, {
      x, y, w: laneW, h: 0.22, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: accent, margin: 0,
    });
    const h = lines.length * 0.27 + 0.14;
    codeBlock(slide, lines, x, y + 0.24, laneW, h, { fontSize: 16 });
    return 0.24 + h;
  }

  function oneLine(x, y, text) {
    slide.addText(text, {
      x, y, w: laneW, h: 0.24, isTextBox: true,
      fontFace: SANS, fontSize: 16, italic: true, color: "3F4E47", margin: 0,
    });
    return 0.24;
  }

  function sampleBox(x, y, h, lines) {
    card(slide, { x, y, w: laneW, h, fill: C.meta, line: "D8D3C6", lineW: 0.75, radius: 0.08 });
    const paras = lines.map((t, i) => ({ text: t, options: { breakLine: i < lines.length - 1 } }));
    slide.addText(paras, {
      x: x + 0.14, y: y + 0.04, w: laneW - 0.28, h: h - 0.08, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: C.text, margin: 0, valign: "top", lineSpacingMultiple: 1.0,
    });
  }

  // Lane 1 — NUTS boundaries
  laneHeader(lanesX[0], "NUTS", "nuts_boundaries.py", C.eea);
  let y1 = laneTop + 0.38 + 0.14;
  y1 += stageCard(lanesX[0], y1, "INGESTION", ["WRITE nuts3_boundaries", "MANIFEST ingestion.json"]) + 0.12;
  y1 += oneLine(lanesX[0], y1, "Validate → SHA-256 → Promote") + 0.1;
  sampleBox(lanesX[0], y1, 0.5, ["DE300 → DE30 → DE3", "PL213 → PL21 → PL2"]);
  y1 += 0.5;

  // Lane 2 — TED codelists
  laneHeader(lanesX[1], "TED Codelists", "codelists.py", C.ted);
  let y2 = laneTop + 0.38 + 0.14;
  y2 += stageCard(lanesX[1], y2, "INGESTION", ["WRITE <id>.gc.xml", "MANIFEST ingestion.json"]) + 0.1;
  y2 += stageCard(lanesX[1], y2, "NORMALIZATION", ["WRITE <id>.parquet", "MANIFEST normalize.json"]) + 0.12;
  y2 += oneLine(lanesX[1], y2, "notice-type · cpv · nuts · currency") + 0.1;
  sampleBox(lanesX[1], y2, 0.5, ["90700000 → Environmental svcs", "EUR → Euro  ·  DEU → Germany"]);
  y2 += 0.5;

  // Lane 3 — EEA stations
  laneHeader(lanesX[2], "EEA Stations", "stations.py", C.eea);
  let y3 = laneTop + 0.38 + 0.14;
  y3 += stageCard(lanesX[2], y3, "INGESTION", ["WRITE stations_raw.json", "MANIFEST ingestion.json"]) + 0.1;
  y3 += stageCard(lanesX[2], y3, "NORMALIZATION", ["WRITE station_metadata", "MANIFEST normalize.json"]) + 0.1;
  y3 += stageCard(lanesX[2], y3, "TRANSFORMATION", ["JOIN nuts3 boundaries", "MANIFEST transform.json"]) + 0.12;
  sampleBox(lanesX[2], y3, 0.4, ["DEBE034 → Berlin → DE300"]);
  y3 += 0.4;

  // ---- Merge + callouts (below the tallest lane, always) -----------------
  const mergeY = Math.max(y1, y2, y3) + 0.14;
  card(slide, { x: 0.4, y: mergeY, w: 12.5, h: 0.34, fill: C.state, line: "00000000", lineW: 0, radius: 0.08 });
  slide.addText("WriteBootstrapManifest → system/bootstrap/reference/latest.json → BootstrapSucceeded | BootstrapFailed", {
    x: 0.6, y: mergeY, w: 12.1, h: 0.34, isTextBox: true,
    fontFace: MONO, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
  });

  const noteY = mergeY + 0.34 + 0.1;
  const notes = ["Manual only · Rare run", "Reference data, not fact data", "Required before Historical + Update"];
  const noteW = 4.0, noteGap = 0.25;
  notes.forEach((n, i) => {
    slide.addText(n, {
      x: 0.4 + i * (noteW + noteGap), y: noteY, w: noteW, h: 0.28, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: accent, align: "center", margin: 0,
    });
  });

  slide.addNotes(
    "Before Historical or Update can process any real data, three pieces of shared reference data must exist: " +
    "NUTS3 region boundaries, TED's eForms codelists (notice type, CPV, NUTS, currency, country and others), " +
    "and EEA station metadata enriched with those same NUTS boundaries. BootstrapReferenceStateMachine runs " +
    "these three branches in parallel, manually, only when needed — this is rare, since reference data changes " +
    "infrequently. NUTS and codelist ingestion use content-hash comparison: download, validate, hash, and only " +
    "promote the file if its content actually changed. Station transformation is the one step here that mirrors " +
    "the main data pipeline's own Transformed layer — it joins station coordinates against the NUTS boundaries " +
    "to add location and NUTS1 through NUTS3. At the end, WriteBootstrapManifest re-checks the real S3 state — " +
    "not just whether each branch reported success — and writes system/bootstrap/reference/latest.json with " +
    "status COMPLETE or INCOMPLETE. Historical and Update both refuse to run at all if this manifest isn't " +
    "COMPLETE, which is why this has to happen first. Every RUN module name is shown once, in each lane's own " +
    "header, since the same file runs every stage in that lane."
  );
};

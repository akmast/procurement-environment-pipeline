module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, shapeNode, codeBlock, sourceBadge, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 9);
  const accent = ACCENT[9];

  title(slide, 9, "Update: Source-Specific Change Detection", { fontSize: 35 });

  // ---- Controller band --------------------------------------------------
  const ctrlY = 1.28, ctrlH = 0.42;
  card(slide, { x: 0.4, y: ctrlY, w: 8.55, h: ctrlH, fill: C.meta, line: "D8D3C6", lineW: 0.75, radius: 0.08 });
  slide.addText("Manual or scheduled start → CheckBootstrapComplete → run_id → RunSources (parallel)", {
    x: 0.6, y: ctrlY, w: 8.15, h: ctrlH, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
  });
  card(slide, { x: 9.1, y: ctrlY, w: 3.8, h: ctrlH, fill: C.aws, line: "00000000", lineW: 0, radius: 0.08 });
  slide.addText("EventBridge: monthly, DISABLED by default", {
    x: 9.25, y: ctrlY, w: 3.5, h: ctrlH, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0, lineSpacingMultiple: 0.95,
  });

  const laneW = 4.0, gap = 0.25;
  const lanesX = [0.4, 0.4 + laneW + gap, 0.4 + 2 * (laneW + gap)];
  const laneTop = ctrlY + ctrlH + 0.14;

  function laneHeader(x, name, sub, fill) {
    card(slide, { x, y: laneTop, w: laneW, h: 0.36, fill, line: "00000000", lineW: 0, radius: 0.08 });
    slide.addText(name, {
      x: x + 0.14, y: laneTop, w: laneW * 0.5, h: 0.36, isTextBox: true,
      fontFace: SANS, fontSize: 17, bold: true, color: C.text, valign: "middle", margin: 0,
    });
    slide.addText(sub, {
      x: x + laneW * 0.42, y: laneTop, w: laneW * 0.56, h: 0.36, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "3F4E47", valign: "middle", align: "right", margin: 0,
    });
    return laneTop + 0.36 + 0.08;
  }

  function step(x, y, header, l1, l2) {
    slide.addText(header, {
      x, y, w: laneW, h: 0.22, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: accent, margin: 0,
    });
    const by = y + 0.24;
    slide.addShape("roundRect", { x, y: by, w: laneW, h: 0.62, fill: { color: "2F3A35" }, line: { type: "none" }, rectRadius: 0.06 });
    slide.addText([
      { text: l1, options: { breakLine: true } },
      { text: l2, options: {} },
    ], {
      x: x + 0.12, y: by + 0.04, w: laneW - 0.24, h: 0.5, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "EDEAE0", valign: "top", margin: 0, lineSpacingMultiple: 1.0,
    });
    return 0.24 + 0.62 + 0.06;
  }

  // EEA
  let ey = laneHeader(lanesX[0], "EEA", "mode=refresh", C.eea);
  ey += step(lanesX[0], ey, "REFRESH WINDOW", "current year: always", "prior year: until 30 Sep");
  ey += step(lanesX[0], ey, "CHANGE CHECK", "validate → SHA-256", "same → skip · diff → replace");
  ey += step(lanesX[0], ey, "DOWNSTREAM", "changed → normalize", "→ transform → gold");
  slide.addText([
    { text: 'state.json: { "content_hash":', options: { breakLine: true } },
    { text: '  "a81f8c8e21f4…" }', options: {} },
  ], {
    x: lanesX[0], y: ey + 0.14, w: laneW, h: 0.5, isTextBox: true,
    fontFace: MONO, fontSize: 16, color: C.text, margin: 0, lineSpacingMultiple: 1.05,
  });

  // TED
  let ty = laneHeader(lanesX[1], "TED", "mode=refresh", C.ted);
  ty += step(lanesX[1], ty, "CURSOR", "date >= cursor", "last_successful_run_date");
  ty += step(lanesX[1], ty, "DEDUP + APPEND", "dedupe by publication-number", "append → notices.jsonl");
  ty += step(lanesX[1], ty, "CURSOR UPDATE", "success → advance cursor", "failure → cursor unchanged");
  slide.addText([
    { text: '{ "last_successful_run_date":', options: { breakLine: true } },
    { text: '  "2025-05-01" }', options: {} },
  ], {
    x: lanesX[1], y: ty + 0.02, w: laneW, h: 0.4, isTextBox: true,
    fontFace: MONO, fontSize: 16, color: C.text, margin: 0, lineSpacingMultiple: 1.0,
  });

  // Eurostat
  let uy = laneHeader(lanesX[2], "Eurostat", "mode=refresh", C.eurostat);
  uy += step(lanesX[2], uy, "RE-QUERY", "years re-requested", "validate JSON-stat");
  uy += step(lanesX[2], uy, "CHANGE CHECK", "SHA-256 → compare content", "same → skip · diff → replace");
  uy += step(lanesX[2], uy, "DOWNSTREAM", "changed → normalize", "(no transform) → gold");
  slide.addText("≥ creates intentional overlap · ID dedup removes repeats", {
    x: lanesX[2], y: uy + 0.02, w: laneW, h: 0.4, isTextBox: true,
    fontFace: SANS, fontSize: 16, italic: true, color: "56655D", margin: 0, lineSpacingMultiple: 1.0,
  });

  // ---- Merge + rule -------------------------------------------------------
  const mergeY = 6.95 - 0.46;
  card(slide, { x: 0.4, y: mergeY, w: 5.6, h: 0.46, fill: C.state, line: "00000000", lineW: 0, radius: 0.08 });
  slide.addText("UpdateSucceeded | UpdateFailed", {
    x: 0.6, y: mergeY, w: 5.2, h: 0.46, isTextBox: true,
    fontFace: MONO, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
  });
  card(slide, { x: 6.15, y: mergeY, w: 6.75, h: 0.46, fill: accent, line: "00000000", lineW: 0, radius: 0.08 });
  slide.addText("No changes → skip  ·  Changes → rebuild Gold", {
    x: 6.35, y: mergeY, w: 6.35, h: 0.46, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.white, valign: "middle", margin: 0,
  });

  slide.addNotes(
    "Update is what the monthly schedule runs, and it's the slide that shows these three sources are not " +
    "interchangeable. EEA and Eurostat both redownload a bounded set of years and compare content by SHA-256 " +
    "hash against a stored state file — identical content is skipped, changed content replaces the file in " +
    "place. EEA's refresh window follows the real reporting deadline: the current year is always re-checked, " +
    "and the prior year stays in scope only until its own 30 September reporting deadline passes. TED works " +
    "completely differently, because it's an append-only notice stream, not a redownloadable snapshot: it reads " +
    "a per-country cursor, last_successful_run_date, and queries strictly for publication-date greater than or " +
    "equal to that cursor — deliberately inclusive, so overlap is expected and handled by deduplicating on " +
    "publication-number, never by trusting the cursor to be exact. The cursor itself only advances after a " +
    "fully successful run; a failed run leaves it untouched so nothing is silently skipped next time. In every " +
    "lane, the same rule governs Gold: no changed paths this run means Gold is skipped for that source; any " +
    "changed paths trigger the same automatic Gold rebuild used in Historical — never a separate step."
  );
};

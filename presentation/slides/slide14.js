module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, bulletList, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 14);
  const accent = ACCENT[14];

  title(slide, 14, "From MVP to Regional Decision Support", { fontSize: 35 });

  // ---- Closing synthesis --------------------------------------------------
  const synY = 1.32, synH = 0.5;
  const steps = ["Open EU data", "Reproducible cloud pipeline", "Regional analytical context", "Local dashboard"];
  const gaps = [2.55, 3.55, 3.55, 2.55];
  let sx = 0.4;
  steps.forEach((s, i) => {
    const w = gaps[i];
    card(slide, { x: sx, y: synY, w, h: synH, fill: i === steps.length - 1 ? accent : C.meta, line: "00000000", lineW: 0, radius: synH / 2 });
    slide.addText(s, {
      x: sx, y: synY, w, h: synH, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: i === steps.length - 1 ? C.white : C.text,
      align: "center", valign: "middle", margin: 0,
    });
    if (i < steps.length - 1) {
      slide.addText("→", {
        x: sx + w, y: synY, w: 0.25, h: synH, isTextBox: true,
        fontFace: SANS, fontSize: 20, bold: true, color: accent, align: "center", valign: "middle", margin: 0,
      });
    }
    sx += w + 0.25;
  });

  // ---- Next steps -----------------------------------------------------------
  const nsY = synY + synH + 0.3;
  slide.addText("NEXT STEPS", {
    x: 0.4, y: nsY, w: 6, h: 0.3, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: accent, charSpacing: 1.5, margin: 0,
  });
  const nextSteps = [
    "Add more countries",
    "Complete NO₂ · O₃ · SO₂ coverage",
    "Add NUTS map visualizations",
    "Strengthen data-quality monitoring",
    "Test cross-source regional consistency",
    "Add water and soil indicators",
    "Deepen procurement-domain validation",
  ];
  const colW = 6.0;
  nextSteps.forEach((s, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    slide.addText("•  " + s, {
      x: 0.4 + col * (colW + 0.5), y: nsY + 0.36 + row * 0.36, w: colW, h: 0.34, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, margin: 0,
    });
  });

  // ---- Contact + final line -------------------------------------------------
  const bottomY = 5.55;
  card(slide, { x: 0.4, y: bottomY, w: 7.6, h: 1.15, fill: C.future, line: "00000000", lineW: 0, radius: 0.12 });
  slide.addText("Better evidence for\nbetter regional decisions", {
    x: 0.65, y: bottomY, w: 7.1, h: 1.15, isTextBox: true,
    fontFace: SANS, fontSize: 26, bold: true, color: C.text, valign: "middle", margin: 0, lineSpacingMultiple: 1.15,
  });

  function qrPlaceholder(x, label) {
    const w = 1.9, boxY = bottomY;
    card(slide, { x, y: boxY, w, h: 1.15, fill: C.white, line: "D8D3C6", lineW: 1, radius: 0.1 });
    slide.addText(label, {
      x, y: boxY + 0.06, w, h: 0.22, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, align: "center", margin: 0,
    });
    const qs = 0.78, qx = x + (w - qs) / 2, qy = boxY + 0.3;
    slide.addShape("rect", { x: qx, y: qy, w: qs, h: qs, fill: { color: "FFFFFF" }, line: { color: C.border, width: 1 } });
    const cell = qs / 5;
    const pattern = [
      [1, 1, 1, 0, 1], [1, 0, 1, 0, 0], [1, 1, 0, 1, 1], [0, 0, 1, 0, 1], [1, 0, 1, 1, 1],
    ];
    for (let r = 0; r < 5; r++) {
      for (let c = 0; c < 5; c++) {
        if (pattern[r][c]) {
          slide.addShape("rect", { x: qx + c * cell, y: qy + r * cell, w: cell, h: cell, fill: { color: C.border }, line: { type: "none" } });
        }
      }
    }
  }
  qrPlaceholder(8.15, "GitHub");
  qrPlaceholder(10.25, "LinkedIn");

  slide.addNotes(
    "To close: this project takes open EU data and turns it into a reproducible cloud pipeline, adds regional " +
    "analytical context by joining NUTS geography across all three sources, and surfaces that through a local " +
    "dashboard. What exists today is a real, working MVP for Germany and Poland — not a mockup. The honest next " +
    "steps: more countries, the remaining EEA pollutants (NO2, O3, SO2 are in the source API but not yet in " +
    "Gold), map-based NUTS visualizations instead of just tables, stronger automated data-quality monitoring, " +
    "testing whether the three sources actually agree with each other at a regional level, and eventually " +
    "adding water and soil indicators alongside air quality. The QR codes are placeholders — swap them for real " +
    "links to the repository and profile before sharing this deck externally. The closing line is deliberately " +
    "the takeaway, not a generic thank-you: better evidence, for better regional decisions."
  );
};

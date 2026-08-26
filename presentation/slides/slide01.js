module.exports = function (pres, H) {
  const { newSlide, card, pill, bodyText, C, ACCENT, SANS } = H;
  const slide = newSlide(pres, 1);
  const accent = ACCENT[1];

  // ---- Title + subtitle ------------------------------------------------
  slide.addText("Procurement & Environment", {
    x: 0.4, y: 0.42, w: 12.5, h: 0.75, isTextBox: true,
    fontFace: SANS, fontSize: 50, bold: true, color: C.text, margin: 0,
  });
  slide.addText("Data Pipeline", {
    x: 0.4, y: 1.12, w: 12.5, h: 0.75, isTextBox: true,
    fontFace: SANS, fontSize: 50, bold: true, color: accent, margin: 0,
  });
  slide.addText("Public spending  ·  Air quality  ·  Agriculture", {
    x: 0.4, y: 1.92, w: 12.5, h: 0.36, isTextBox: true,
    fontFace: SANS, fontSize: 18, color: C.text, margin: 0,
  });
  slide.addText("Germany  ·  Poland  ·  AWS", {
    x: 0.4, y: 2.26, w: 12.5, h: 0.34, isTextBox: true,
    fontFace: SANS, fontSize: 18, color: "5B6C64", margin: 0,
  });

  // ---- Three-part composition ------------------------------------------
  const rowY = 2.85, rowH = 2.55;
  const colW = 3.75, gap = 0.25;
  const leftX = 0.4;
  const centerX = leftX + colW + gap;
  const rightX = centerX + colW + gap + 0.9;

  function scene(x, y, w, h, fill, label, icon) {
    card(slide, { x, y, w, h, fill, line: "00000000", lineW: 0, radius: 0.1 });
    icon(x, y, w, h);
    slide.addText(label, {
      x: x + 0.12, y: y + h - 0.36, w: w - 0.24, h: 0.28, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, margin: 0,
    });
  }

  const sceneH = (rowH - 0.2) / 3;
  // Left: dry field / city haze / monitoring station
  scene(leftX, rowY, colW, sceneH, C.eurostat, "Dry field", (x, y, w, h) => {
    for (let i = 0; i < 4; i++) {
      slide.addShape("line", {
        x: x + 0.18, y: y + 0.18 + i * 0.14, w: w - 0.36, h: 0,
        line: { color: "9CC79F", width: 1.5 },
      });
    }
  });
  scene(leftX, rowY + sceneH + 0.1, colW, sceneH, C.aws, "City haze", (x, y, w, h) => {
    const bw = 0.26, maxBh = h - 0.44;
    [0.35, 0.55, 0.75, 1.0].forEach((frac, i) => {
      const bh = maxBh * frac;
      slide.addShape("rect", {
        x: x + 0.2 + i * (bw + 0.06), y: y + (h - 0.36) - bh, w: bw, h: bh,
        fill: { color: "F2B98A" }, line: { type: "none" },
      });
    });
    slide.addShape("line", {
      x: x + 0.15, y: y + 0.16, w: w - 0.3, h: 0,
      line: { color: "F2E3D2", width: 3, dashType: "sysDot" },
    });
  });
  scene(leftX, rowY + 2 * (sceneH + 0.1), colW, sceneH, C.eea, "Monitoring station", (x, y, w, h) => {
    slide.addShape("line", {
      x: x + w / 2, y: y + 0.2, w: 0, h: h - 0.5,
      line: { color: C.border, width: 2 },
    });
    slide.addShape("ellipse", {
      x: x + w / 2 - 0.12, y: y + 0.1, w: 0.24, h: 0.24,
      fill: { color: C.border }, line: { type: "none" },
    });
  });

  // Center: problem statement
  const cCardH = rowH;
  card(slide, { x: centerX, y: rowY, w: colW + 0.5, h: cCardH, fill: C.white, line: "D8D3C6", lineW: 1, radius: 0.12,
    shadow: { type: "outer", color: "999999", opacity: 0.18, blur: 6, offset: 2, angle: 90 } });
  slide.addText("THE PROBLEM", {
    x: centerX + 0.28, y: rowY + 0.24, w: colW, h: 0.32, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: accent, charSpacing: 1.5, margin: 0,
  });
  bodyText(slide,
    "Public money must meet regional needs.",
    centerX + 0.28, rowY + 0.68, colW, 0.85,
    { fontSize: 20, bold: true, lineSpacingMultiple: 1.15 });
  bodyText(slide,
    "The evidence is split across systems.",
    centerX + 0.28, rowY + 1.65, colW, 0.85,
    { fontSize: 20, bold: true, lineSpacingMultiple: 1.15, color: accent });

  // Right: institution / procurement doc / investment
  scene(rightX, rowY, colW, sceneH, C.state, "Institution", (x, y, w, h) => {
    slide.addShape("triangle", {
      x: x + w / 2 - 0.32, y: y + 0.14, w: 0.64, h: 0.34,
      fill: { color: "B7B0EA" }, line: { type: "none" },
    });
    slide.addShape("rect", {
      x: x + w / 2 - 0.32, y: y + 0.48, w: 0.64, h: 0.3,
      fill: { color: "C9C4EF" }, line: { type: "none" },
    });
  });
  scene(rightX, rowY + sceneH + 0.1, colW, sceneH, C.ted, "Contract", (x, y, w, h) => {
    slide.addShape("rect", {
      x: x + w / 2 - 0.24, y: y + 0.14, w: 0.48, h: 0.58,
      fill: { color: C.white }, line: { color: "E6D68F", width: 1 },
    });
    [0.26, 0.36, 0.46].forEach((yy) => {
      slide.addShape("line", {
        x: x + w / 2 - 0.16, y: y + yy, w: 0.32, h: 0,
        line: { color: "D8C471", width: 1.5 },
      });
    });
  });
  scene(rightX, rowY + 2 * (sceneH + 0.1), colW, sceneH, C.success, "Green investment", (x, y, w, h) => {
    slide.addShape("line", {
      x: x + w / 2 - 0.22, y: y + 0.55, w: 0.44, h: -0.35,
      line: { color: "6FA97D", width: 3, endArrowType: "triangle" },
    });
  });

  // ---- Four pills --------------------------------------------------------
  const pillY = 5.62, pillH = 0.5, pillW = 2.9, pillGap = 0.2;
  const pillsTotalW = 4 * pillW + 3 * pillGap;
  const startX = 0.4 + ((12.5 - pillsTotalW) / 2);
  ["Separate APIs", "Different formats", "Different regions", "Different updates"].forEach((t, i) => {
    pill(slide, t, startX + i * (pillW + pillGap), pillY, pillW, pillH, C.meta, { fontSize: 16 });
  });

  // ---- Goal card -----------------------------------------------------
  const goalY = 6.32, goalH = 0.78;
  card(slide, { x: 0.4, y: goalY, w: 12.5, h: goalH, fill: accent, line: "00000000", lineW: 0, radius: 0.12 });
  slide.addText("GOAL", {
    x: 0.65, y: goalY + 0.1, w: 1.6, h: goalH - 0.2, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.white, valign: "middle", margin: 0,
  });
  slide.addText("Connect public spending, air quality and agriculture.", {
    x: 2.2, y: goalY, w: 10.4, h: goalH, isTextBox: true,
    fontFace: SANS, fontSize: 20, bold: true, color: C.white, valign: "middle", margin: 0,
  });

  slide.addNotes(
    "This is the title slide. The project connects three open European datasets — public procurement (TED), " +
    "air quality (EEA), and regional agriculture (Eurostat) — for Germany and Poland, built and deployed on AWS. " +
    "The core problem: public money is supposed to respond to regional environmental and economic needs, but the " +
    "evidence needed to check that lives in three unrelated systems, each with its own API, format, region " +
    "granularity, and update cadence. The goal of this project is a reproducible cloud pipeline that joins these " +
    "into one queryable, regionally-comparable dataset — not to prove causation, but to make the evidence " +
    "comparable side by side."
  );
};

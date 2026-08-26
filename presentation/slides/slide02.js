module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, C, ACCENT, SANS } = H;
  const slide = newSlide(pres, 2);
  const accent = ACCENT[2];

  title(slide, 2, "Who Needs the Answer?");

  const stakeholders = [
    { name: "Public Authorities", sub: "Regional planning · Policy checks", color: C.eea },
    { name: "Environmental Agencies", sub: "Pollution trends · Regional pressure", color: C.eurostat },
    { name: "Agricultural Analysts", sub: "Farm activity · Regional output", color: C.gold },
    { name: "Procurement Teams", sub: "Tender activity · Public spending", color: C.ted },
    { name: "Researchers", sub: "Cross-domain analysis · Regional patterns", color: C.state },
    { name: "NGOs", sub: "Public oversight · Sustainability tracking", color: C.future },
  ];

  const gridTop = 1.42, cardW = 4.0, cardH = 1.5, gapX = 0.17, gapY = 0.14;
  stakeholders.forEach((s, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.4 + col * (cardW + gapX);
    const y = gridTop + row * (cardH + gapY);
    card(slide, { x, y, w: cardW, h: cardH, fill: C.white, line: "D8D3C6", lineW: 1, radius: 0.1 });
    slide.addShape("ellipse", {
      x: x + 0.2, y: y + 0.18, w: 0.36, h: 0.36,
      fill: { color: s.color }, line: { type: "none" },
    });
    slide.addText(s.name, {
      x: x + 0.66, y: y + 0.12, w: cardW - 0.86, h: 0.7, isTextBox: true,
      fontFace: SANS, fontSize: 24, bold: true, color: C.text, margin: 0,
      valign: "top", lineSpacingMultiple: 0.98,
    });
    slide.addText(s.sub, {
      x: x + 0.2, y: y + cardH - 0.58, w: cardW - 0.4, h: 0.5, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: "56655D", margin: 0, valign: "top",
    });
  });

  // Four questions
  const qY = gridTop + 2 * (cardH + gapY) + 0.12;
  const questions = [
    "Where is air quality worse?",
    "Where is farm activity strongest?",
    "Where does green spending go?",
    "Do spending and needs align?",
  ];
  const qW = 2.99, qGap = 0.17, qH = 0.82;
  questions.forEach((q, i) => {
    const x = 0.4 + i * (qW + qGap);
    card(slide, { x, y: qY, w: qW, h: qH, fill: accent, line: "00000000", lineW: 0, radius: 0.1 });
    slide.addText(q, {
      x: x + 0.16, y: qY, w: qW - 0.32, h: qH, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.white,
      valign: "middle", margin: 0, lineSpacingMultiple: 1.0,
    });
  });

  // Caution strip
  const cY = qY + qH + 0.16;
  card(slide, { x: 0.4, y: cY, w: 12.5, h: 0.5, fill: C.meta, line: "D8D3C6", lineW: 1, radius: 0.1 });
  slide.addText("⚠  Pattern, not causation", {
    x: 0.65, y: cY, w: 12.0, h: 0.5, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text,
    valign: "middle", margin: 0,
  });

  slide.addNotes(
    "Six kinds of users benefit from combining these datasets: public authorities doing regional planning, " +
    "environmental agencies tracking pollution, agricultural analysts, procurement teams, cross-domain " +
    "researchers, and NGOs doing public oversight. Together they ask four recurring questions: where air " +
    "quality is worse, where farm activity is strongest, where green-labeled spending actually goes, and " +
    "whether spending lines up with regional need. The important caveat, worth stating out loud: this dashboard " +
    "shows regional patterns side by side — it does not prove that one variable caused another. A region with " +
    "both high spending and poor air quality is a pattern worth investigating, not proof of failure or success."
  );
};

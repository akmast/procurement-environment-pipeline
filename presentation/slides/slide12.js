module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, bulletList, vArrow, shapeNode, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 12);
  const accent = ACCENT[12];

  title(slide, 12, "Local Metabase Queries Athena", { fontSize: 35 });

  // ---- Left: connection flow ---------------------------------------------
  const lx = 0.4, lw = 5.4;
  let y = 1.42;
  const nodes = [
    { t: "Local Metabase container", sub: "http://localhost:3000", fill: C.state },
    { t: "Athena workgroup", sub: "Amazon Athena driver", fill: C.aws },
    { t: "Gold Parquet in S3", sub: "via Glue table schema", fill: C.meta },
  ];
  const nH = 0.56, nGap = 0.42;
  nodes.forEach((n, i) => {
    shapeNode(slide, "roundRect", null, lx, y, lw, nH, n.fill, { radius: 0.09 });
    slide.addText(n.t, {
      x: lx + 0.18, y: y + 0.05, w: lw - 0.36, h: 0.3, isTextBox: true,
      fontFace: SANS, fontSize: 17, bold: true, color: C.text, margin: 0,
    });
    slide.addText(n.sub, {
      x: lx + 0.18, y: y + 0.32, w: lw - 0.36, h: 0.22, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "3F4E47", margin: 0,
    });
    if (i < nodes.length - 1) vArrow(slide, lx + nH * 0.5, y + nH, y + nH + nGap, {});
    y += nH + nGap;
  });

  const authY = y + 0.06;
  card(slide, { x: lx, y: authY, w: lw, h: 0.9, fill: C.future, line: "00000000", lineW: 0, radius: 0.1 });
  slide.addText("Local AWS CLI / SSO profile\n→ mounted read-only\n→ AWS credentials provider chain", {
    x: lx + 0.2, y: authY + 0.08, w: lw - 0.4, h: 0.74, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, margin: 0, lineSpacingMultiple: 1.15, valign: "middle",
  });

  const runtimeY = authY + 0.9 + 0.2;
  const facts = ["Started manually · stopped manually", "No public endpoint", "Dashboard state in Docker volume", "Gold data remains in S3"];
  facts.forEach((f, i) => {
    slide.addText("•  " + f, {
      x: lx, y: runtimeY + i * 0.27, w: lw, h: 0.26, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, margin: 0,
    });
  });

  // ---- Right: dashboard mockup --------------------------------------------
  const rx = 6.15, rw = 6.75;
  const mockY = 1.42, mockH = 5.1;
  card(slide, { x: rx, y: mockY, w: rw, h: mockH, fill: C.white, line: "D8D3C6", lineW: 1, radius: 0.12,
    shadow: { type: "outer", color: "999999", opacity: 0.15, blur: 6, offset: 2, angle: 90 } });

  // Filter pill
  slide.addShape("roundRect", { x: rx + 0.24, y: mockY + 0.2, w: 2.7, h: 0.4, fill: { color: C.meta }, line: { color: "D8D3C6", width: 0.75 }, rectRadius: 0.2 });
  slide.addText("Country:  DE | PL", {
    x: rx + 0.42, y: mockY + 0.2, w: 2.4, h: 0.4, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
  });

  const gridY = mockY + 0.8, gridPad = 0.24, cellW = (rw - gridPad * 3) / 2, cellH = (mockH - 0.8 - gridPad * 3) / 2;
  function panel(px, py, label, fill, draw) {
    card(slide, { x: px, y: py, w: cellW, h: cellH, fill: C.bg, line: "E3DFD3", lineW: 1, radius: 0.08 });
    slide.addText(label, {
      x: px + 0.14, y: py + 0.08, w: cellW - 0.28, h: 0.24, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, margin: 0,
    });
    draw(px, py);
  }

  panel(rx + gridPad, gridY, "Air quality by month · pollutant", C.eea, (px, py) => {
    const baseY = py + cellH - 0.22, baseX = px + 0.3, bw = 0.22;
    [0.5, 0.8, 0.65, 1.0, 0.7, 0.9].forEach((h, i) => {
      slide.addShape("rect", { x: baseX + i * (bw + 0.1), y: baseY - h * 0.9, w: bw, h: h * 0.9, fill: { color: C.eea }, line: { type: "none" } });
    });
  });
  panel(rx + gridPad * 2 + cellW, gridY, "Tender count by month", C.ted, (px, py) => {
    const pts = [[0.3, 0.6], [0.9, 0.4], [1.5, 0.7], [2.1, 0.3], [2.7, 0.55]];
    for (let i = 0; i < pts.length - 1; i++) {
      slide.addShape("line", {
        x: px + pts[i][0], y: py + 0.5 + pts[i][1], w: pts[i + 1][0] - pts[i][0], h: pts[i + 1][1] - pts[i][1],
        line: { color: "8A6D1D", width: 2.5 },
      });
    }
  });
  panel(rx + gridPad, gridY + cellH + gridPad, "Procurement value by month", C.gold, (px, py) => {
    const baseY = py + cellH - 0.22, baseX = px + 0.3, bw = 0.22;
    [0.4, 0.55, 0.9, 0.6, 0.75, 1.0].forEach((h, i) => {
      slide.addShape("rect", { x: baseX + i * (bw + 0.1), y: baseY - h * 0.9, w: bw, h: h * 0.9, fill: { color: "E8BE5C" }, line: { type: "none" } });
    });
  });
  panel(rx + gridPad * 2 + cellW, gridY + cellH + gridPad, "Top agricultural items / NUTS2", C.eurostat, (px, py) => {
    const baseX = px + 0.3, barH = 0.22;
    [1.9, 1.5, 1.1, 0.7].forEach((w, i) => {
      slide.addShape("rect", { x: baseX, y: py + 0.42 + i * 0.3, w, h: barH, fill: { color: "6FA97D" }, line: { type: "none" } });
    });
  });

  slide.addNotes(
    "Metabase runs entirely locally, in Docker — this is the boundary between the AWS-hosted analytics stack " +
    "and the visualization layer on someone's own machine. It authenticates to Athena using the operator's " +
    "existing local AWS CLI or SSO profile, mounted into the container read-only; there are no static access " +
    "keys baked into the image or the compose file, just the standard AWS credentials provider chain. The " +
    "dashboard mockup shown here is illustrative — a single Country filter driving four views that are each " +
    "backed directly by one Gold table's own columns: air quality by month and pollutant from EEA, tender " +
    "count and procurement value by month from TED, and top agricultural items or NUTS2 regions from Eurostat. " +
    "Nothing about Metabase changes the data itself — Gold stays in S3 exactly as Historical/Update wrote it; " +
    "Metabase only reads it through Athena. It's started and stopped manually, has no public endpoint, and its " +
    "own state — saved questions, dashboards — lives in a Docker volume, separate from the data."
  );
};

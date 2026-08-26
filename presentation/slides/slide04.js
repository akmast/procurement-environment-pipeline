module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, shapeNode, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 4);

  title(slide, 4, "One Architecture, Four Data Layers", { fontSize: 35 });

  const nodeW = 3.6, nodeH = 0.56;
  const cx = 5.0 - nodeW / 2;
  let y = 1.42;
  const gap = 0.5;

  const layers = [
    { name: "RAW", sub: "JSON · JSONL · Parquet", fill: C.meta,
      callout: "Byte-for-byte source fidelity" },
    { name: "NORMALIZED", sub: "Clean names · fixed types", fill: C.state,
      callout: "Consistent names + types" },
    { name: "TRANSFORMED", sub: "+ Labels · station data", fill: C.future,
      callout: "Human labels + regional codes" },
    { name: "GOLD", sub: "One fixed table per source", fill: C.gold,
      callout: "Dashboard's actual contract" },
    { name: "DASHBOARD", sub: "Country · Time · Region", fill: C.success,
      callout: "Athena, then any BI tool" },
  ];

  const arrowLabels = ["Rename · Parse · Validate", "Join refs · Add regions", "Select · Rename · Dedup", "SQL over S3"];

  layers.forEach((l, i) => {
    shapeNode(slide, "roundRect", null, cx, y, nodeW, nodeH, l.fill, { radius: 0.09 });
    slide.addText(l.name, {
      x: cx, y, w: nodeW, h: nodeH, isTextBox: true,
      fontFace: SANS, fontSize: 24, bold: true, color: C.text, align: "center", valign: "middle", margin: 0,
    });
    slide.addText(l.sub, {
      x: cx + nodeW + 0.35, y: y - 0.04, w: 3.55, h: 0.3, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
    });
    slide.addText(l.callout, {
      x: cx + nodeW + 0.35, y: y + 0.32, w: 3.55, h: 0.24, isTextBox: true,
      fontFace: SANS, fontSize: 16, italic: true, color: "56655D", valign: "top", margin: 0,
    });
    if (i < layers.length - 1) {
      vArrow(slide, cx + nodeW / 2, y + nodeH, y + nodeH + gap, {});
      slide.addText(arrowLabels[i], {
        x: cx, y: y + nodeH + gap / 2 - 0.13, w: nodeW, h: 0.26,
        isTextBox: true, fontFace: MONO, fontSize: 16, color: C.border, align: "center", valign: "middle", margin: 0,
      });
    }
    if (i < layers.length - 1) y += nodeH + gap;
  });

  // Technology rail
  const railY = y + nodeH + 0.3;
  slide.addText("Python  ·  Docker  ·  AWS  ·  Terraform  ·  GitHub Actions", {
    x: 0.4, y: railY, w: 12.5, h: 0.26, isTextBox: true,
    fontFace: MONO, fontSize: 16, color: C.border, align: "center", valign: "middle", margin: 0,
  });

  slide.addNotes(
    "Every source moves through the same four-layer model before it reaches the dashboard. Raw keeps exactly " +
    "what the API returned, for fidelity and replay. Normalized renames fields and fixes types so every source " +
    "looks structurally similar. Transformed joins in reference data — for EEA that means station location and " +
    "NUTS1 through NUTS3 codes; for TED it means NUTS and CPV labels. Gold is the layer the dashboard actually " +
    "queries: one fixed, documented schema per source, in S3 as Parquet. Eurostat skips the Transformed step " +
    "entirely — its normalization step already produces analysis-ready rows, so its Gold build reads straight " +
    "from Normalized; that's a real property of this pipeline, not a simplification for the slide. Everything " +
    "left of Dashboard is plain Python running in Docker on AWS, deployed by Terraform through GitHub Actions."
  );
};

module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, shapeNode, sourceBadge, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 11);
  const accent = ACCENT[11];

  title(slide, 11, "Glue and Athena Expose Gold as SQL", { fontSize: 35 });

  const nodeW = 4.4, nodeH = 0.62;
  const cx = 3.4 - nodeW / 2 + 0.4;
  let y = 1.5;
  const gap = 0.5;

  const flow = [
    { t: "Gold Parquet in S3", sub: "data/gold/<source>/*.parquet", fill: C.meta },
    { t: "Glue Data Catalog", sub: "procurement_gold · 3 tables", fill: C.state },
    { t: "Athena Workgroup", sub: "<project>-gold", fill: C.aws },
    { t: "Query results", sub: "s3://<bucket>/athena-results/", fill: C.success },
  ];
  flow.forEach((n, i) => {
    shapeNode(slide, "roundRect", null, cx, y, nodeW, nodeH, n.fill, { radius: 0.09 });
    slide.addText(n.t, {
      x: cx + 0.2, y: y + 0.06, w: nodeW - 0.4, h: 0.32, isTextBox: true,
      fontFace: SANS, fontSize: 18, bold: true, color: C.text, margin: 0,
    });
    slide.addText(n.sub, {
      x: cx + 0.2, y: y + 0.36, w: nodeW - 0.4, h: 0.24, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "3F4E47", margin: 0,
    });
    if (i < flow.length - 1) vArrow(slide, cx + nodeW / 2, y + nodeH, y + nodeH + gap, {});
    y += nodeH + gap;
  });

  const noteY = y + 0.1;
  slide.addText("Tables are defined explicitly in Terraform (glue.tf) — not discovered by a crawler.\nEach Gold build already produces one fixed, documented schema.", {
    x: cx - 0.2, y: noteY, w: nodeW + 0.4, h: 0.6, isTextBox: true,
    fontFace: SANS, fontSize: 16, italic: true, color: "56655D", align: "center", margin: 0, lineSpacingMultiple: 1.1,
  });

  // ---- Right: table labels + callouts -----------------------------------
  const rx = 8.5, rw = 4.4;
  const tables = [
    { name: "eea_measurements", label: "EEA measurements", fill: C.eea },
    { name: "ted_notices", label: "TED notices", fill: C.ted },
    { name: "eurostat_agriculture_accounts", label: "Eurostat agriculture accounts", fill: C.eurostat },
  ];
  let ty = 1.5;
  tables.forEach((t) => {
    card(slide, { x: rx, y: ty, w: rw, h: 0.72, fill: t.fill, line: "00000000", lineW: 0, radius: 0.1 });
    slide.addText(t.label, {
      x: rx + 0.18, y: ty + 0.08, w: rw - 0.36, h: 0.26, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, margin: 0,
    });
    slide.addText(t.name, {
      x: rx + 0.18, y: ty + 0.36, w: rw - 0.36, h: 0.28, isTextBox: true,
      fontFace: MONO, fontSize: 16, color: "3F4E47", margin: 0,
    });
    ty += 0.72 + 0.16;
  });

  const calloutY = ty + 0.1;
  const callouts = ["Glue stores schema, not data", "Athena reads Parquet in place", "Pay per data scanned"];
  callouts.forEach((c, i) => {
    const cy = calloutY + i * 0.42;
    slide.addShape("ellipse", { x: rx, y: cy + 0.06, w: 0.1, h: 0.1, fill: { color: accent }, line: { type: "none" } });
    slide.addText(c, {
      x: rx + 0.24, y: cy, w: rw - 0.24, h: 0.34, isTextBox: true,
      fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
    });
  });

  slide.addNotes(
    "Glue and Athena turn the Gold Parquet files into ordinary SQL tables without ever moving or copying the " +
    "data. Terraform defines the Glue Catalog explicitly — one database, procurement_gold by default, and three " +
    "external tables, one per source, each pointing straight at that source's Gold folder in S3. This is a " +
    "deliberate choice over a Glue Crawler: every Gold build already produces one fixed, fully-documented " +
    "schema, so a crawler would only add ongoing cost and IAM surface for schema inference this project doesn't " +
    "need. If a Gold column ever changes, the matching Glue table has to be updated in the same change, or " +
    "queries silently return nulls or wrong types instead of erroring. Athena is a single serverless workgroup " +
    "over that catalog — no cluster to provision or pay for when nobody's querying. Query results land in the " +
    "same data bucket, under athena-results/, with a lifecycle rule that expires them automatically. Cost is " +
    "purely pay-per-query, based on how much data that query actually scans."
  );
};

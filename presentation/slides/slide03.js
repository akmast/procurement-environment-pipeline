module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, codeBlock, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 3);

  title(slide, 3, "Three Sources, Three Structures", { fontSize: 35 });

  const laneW = 4.0, gap = 0.25;
  const lanesX = [0.4, 0.4 + laneW + gap, 0.4 + 2 * (laneW + gap)];
  const topY = 1.35;

  function laneHeader(x, name, sub, fill) {
    card(slide, { x, y: topY, w: laneW, h: 0.58, fill, line: "00000000", lineW: 0, radius: 0.1 });
    slide.addText(name, {
      x: x + 0.2, y: topY + 0.04, w: laneW - 0.4, h: 0.32, isTextBox: true,
      fontFace: SANS, fontSize: 22, bold: true, color: C.text, margin: 0,
    });
    slide.addText(sub, {
      x: x + 0.2, y: topY + 0.33, w: laneW - 0.4, h: 0.24, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: "3F4E47", margin: 0,
    });
  }

  function lines(x, y, arr, opts) {
    const o = opts || {};
    const paras = arr.map((t, i) => ({
      text: t,
      options: { breakLine: i < arr.length - 1, fontSize: o.fontSize || 16, bold: !!o.bold, color: o.color || C.text },
    }));
    slide.addText(paras, {
      x, y, w: laneW, h: o.h || (arr.length * 0.25 + 0.05), isTextBox: true,
      fontFace: o.mono ? MONO : SANS, valign: "top", margin: 0, lineSpacingMultiple: 1.05,
    });
    return (o.h || (arr.length * 0.25 + 0.05));
  }

  function buildLane(x, cfg) {
    let y = topY;
    laneHeader(x, cfg.name, cfg.sub, cfg.fill);
    y += 0.58 + 0.1;

    y += lines(x, y, cfg.facts, { fontSize: 16 }) + 0.1;
    y += lines(x, y, [cfg.metrics], { fontSize: 16, bold: true, color: cfg.accentText }) + 0.12;

    // Request
    slide.addText(cfg.request, {
      x, y, w: laneW, h: 0.24, isTextBox: true, fontFace: MONO, fontSize: 16,
      bold: true, color: C.border, margin: 0,
    });
    y += 0.3;
    const jsonH = cfg.json.length * 0.27 + 0.2;
    codeBlock(slide, cfg.json, x, y, laneW, jsonH, { fontSize: 16 });
    y += jsonH + 0.12;

    // Sample
    card(slide, { x, y, w: laneW, h: 0.56, fill: C.meta, line: "D8D3C6", lineW: 0.75, radius: 0.08 });
    const sPar = cfg.sample.map((t, i) => ({
      text: t, options: { breakLine: i < cfg.sample.length - 1, fontSize: 16, color: C.text },
    }));
    slide.addText(sPar, {
      x: x + 0.14, y: y + 0.04, w: laneW - 0.28, h: 0.5, isTextBox: true,
      fontFace: MONO, valign: "top", margin: 0, lineSpacingMultiple: 1.0,
    });
    y += 0.56 + 0.16;

    // Stats
    const statPar = cfg.stats.map((t, i) => ({
      text: t, options: { breakLine: i < cfg.stats.length - 1, fontSize: 16, bold: true, color: cfg.accentText },
    }));
    slide.addText(statPar, {
      x, y, w: laneW, h: 0.76, isTextBox: true,
      fontFace: SANS, valign: "top", margin: 0, lineSpacingMultiple: 1.12,
    });
  }

  buildLane(lanesX[0], {
    name: "EEA", sub: "Air Quality", fill: C.eea, accentText: "1D5F8A",
    facts: ["Day × Station × Pollutant", "Parquet · File URL API", "DE · PL"],
    metrics: "PM10 · PM2.5 · NO₂ · O₃ · SO₂",
    request: "POST /ParquetFile/urls",
    json: ["{", '  "countries": ["DE"],', '  "pollutants": ["PM10"],', '  "aggregationType": "day"', "}"],
    sample: ["DEBE034 · PM10 · 18.4", "2025-01-01 · ug.m-3"],
    stats: ["54,099 rows · 14 columns", "DE 53,796 · PL 303", "Gold: PM10 + PM2.5"],
  });

  buildLane(lanesX[1], {
    name: "TED", sub: "Public Procurement", fill: C.ted, accentText: "8A6D1D",
    facts: ["One notice per record", "JSON → JSONL · Search API", "DE · PL"],
    metrics: "Buyer · CPV · NUTS · Value",
    request: "POST /v3/notices/search",
    json: ["{", '  "limit": 250,', '  "latest": true', "}"],
    sample: ["123456-2025 · DE30", "1,250,000 EUR"],
    stats: ["61,794 notices · 13 columns", "DE 24,154 · PL 37,640", "2021–2026"],
  });

  buildLane(lanesX[2], {
    name: "Eurostat", sub: "Regional Agriculture", fill: C.eurostat, accentText: "2E7A4A",
    facts: ["Year × NUTS2 × Item", "JSON-stat 2.0", "Dataset: aact_eaa01_r"],
    metrics: "Item · Indicator · Region · Year",
    request: "GET /…/data/aact_eaa01_r",
    json: ["{", '  "size": [1,1,1,2,2,1],', '  "value": {"0":273.94}', "}"],
    sample: ["DE11 · Cereals · 2023", "273.94 Million EUR"],
    stats: ["27,585 rows · 12 columns", "DE 19,904 · PL 7,681", "DE 2021–23 · PL 2021,23"],
  });

  // Bottom strip
  const stripY = 6.98;
  slide.addText([
    { text: "Not used in MVP:  ", options: { bold: true, color: C.text } },
    { text: "National-only · Broad regions · Old releases · Restricted access", options: { color: "56655D" } },
  ], {
    x: 0.4, y: stripY - 0.02, w: 12.5, h: 0.26, isTextBox: true,
    fontFace: SANS, fontSize: 16, margin: 0, valign: "middle",
  });

  slide.addNotes(
    "Answering the four questions from the previous slide means pulling from three genuinely different systems. " +
    "EEA publishes air-quality measurements as day-by-station-by-pollutant Parquet files behind a file-URL API " +
    "— we request PM10 and PM2.5 for Germany and Poland. TED, the EU's public procurement portal, returns one " +
    "JSON notice per contract award via a search API; we flatten that into JSONL. Eurostat's regional " +
    "agriculture accounts come back as a JSON-stat 2.0 data cube — a flat value array plus dimension sizes that " +
    "have to be decoded into row coordinates before they're usable, shown in the code sample as size/value. " +
    "Each source also differs in region granularity, update cadence, and how 'the same data' is versioned. The " +
    "statistics shown are the current ingested snapshot for DE/PL, not the full extent of either API — some " +
    "national-only or broad-region data, and older archived releases, are deliberately out of scope for this " +
    "MVP. The visible request/response samples here are shortened for space; exact field names are verified " +
    "against the ingestion code in docs/pipelines/*.md."
  );
};

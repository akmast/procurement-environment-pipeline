module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, shapeNode, codeBlock, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 6);

  title(slide, 6, "One Fargate Task Runs One Stage", { fontSize: 35 });

  // ---- Left: runtime sequence -------------------------------------------
  const lx = 0.4, lw = 6.1;
  let y = 1.4;
  const rowH = 0.5, rowGap = 0.3;

  const steps = [
    { t: "Step Functions state\nEeaRunNormalization", fill: C.state },
    { t: "ecs:RunTask.sync  →  Fargate task", fill: C.aws, arrowLabel: "pull image from ECR" },
    { t: "main.py stage\none source · one stage · one run_id", fill: C.white, arrowLabel: "override command" },
    { t: "Python source module:\nread S3 → process → write S3", fill: C.eea, arrowLabel: "read manifest" },
    { t: "Stage manifest written to S3", fill: C.meta, arrowLabel: "StageResult" },
    { t: "Step Functions continues", fill: C.success, arrowLabel: "container exits" },
  ];

  steps.forEach((s, i) => {
    const h = s.t.includes("\n") ? rowH + 0.24 : rowH;
    shapeNode(slide, "roundRect", null, lx, y, lw, h, s.fill, { radius: 0.08, line: C.border, lineW: 0.75 });
    const lines = s.t.split("\n");
    const paras = lines.map((ln, j) => ({
      text: ln, options: { breakLine: j < lines.length - 1, bold: j === 0, fontSize: 16, color: C.text },
    }));
    slide.addText(paras, {
      x: lx + 0.18, y, w: lw - 0.36, h, isTextBox: true,
      fontFace: SANS, valign: "middle", margin: 0, lineSpacingMultiple: 1.05,
    });
    const top = y;
    y += h + rowGap;
    if (i < steps.length - 1) {
      vArrow(slide, lx + 0.3, top + h, y, {});
      if (s.arrowLabel) {
        slide.addText(s.arrowLabel, {
          x: lx + 0.5, y: top + h + 0.05, w: lw - 0.6, h: 0.2,
          isTextBox: true, fontFace: MONO, fontSize: 16, color: C.border, valign: "middle", margin: 0,
        });
      }
    }
  });

  // ---- Right: manifest example -------------------------------------------
  const rx = 6.75, rw = 6.15;
  slide.addText([
    { text: "Stage manifest", options: { breakLine: true } },
    { text: "runs/<run_id>/<source>/<stage>.json", options: {} },
  ], {
    x: rx, y: 1.28, w: rw, h: 0.5, isTextBox: true,
    fontFace: MONO, fontSize: 16, bold: true, color: C.border, margin: 0, lineSpacingMultiple: 1.05,
  });
  const manifest = [
    "{",
    '  "written_paths": ["…/PM10/file.parquet"],',
    '  "changed_paths": ["…/PM10/file.parquet"],',
    '  "unchanged_paths": [], "failed_paths": [],',
    '  "status": "SUCCEEDED",',
    '  "run_id": "3f2a9c1e-…-4f90ab",',
    '  "source": "eea-measurements",',
    '  "stage": "normalization",',
    '  "countries": ["DE", "PL"]',
    "}",
  ];
  const mH = manifest.length * 0.27 + 0.22;
  const codeY = 1.88;
  codeBlock(slide, manifest, rx, codeY, rw, mH, { fontSize: 16 });

  const calloutsY = codeY + mH + 0.22;
  const callouts = [
    "Large data stays in S3",
    "Stage handoff happens through written_paths",
    "No EC2 · No Lambda",
    "No ECS service · No idle compute",
  ];
  callouts.forEach((c, i) => {
    const cy = calloutsY + i * 0.3;
    slide.addShape("ellipse", { x: rx, y: cy + 0.06, w: 0.1, h: 0.1, fill: { color: ACCENT[6] }, line: { type: "none" } });
    slide.addText(c, {
      x: rx + 0.22, y: cy, w: rw - 0.22, h: 0.26, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: C.text, margin: 0, valign: "middle",
    });
  });

  const netY = calloutsY + callouts.length * 0.3 + 0.12;
  card(slide, { x: rx, y: netY, w: rw, h: 0.56, fill: C.meta, line: "D8D3C6", lineW: 0.75, radius: 0.1 });
  slide.addText("Public subnet · Public IP · Outbound HTTPS only\nNo inbound rules · No NAT Gateway", {
    x: rx + 0.18, y: netY, w: rw - 0.36, h: 0.56, isTextBox: true,
    fontFace: SANS, fontSize: 16, color: C.text, valign: "middle", margin: 0, lineSpacingMultiple: 1.05,
  });

  slide.addNotes(
    "This is the runtime contract every single stage in this pipeline follows, no matter which source or stage " +
    "it is. Step Functions doesn't run any Python itself — it calls ecs:RunTask.sync, which starts one " +
    "temporary Fargate task, pulls the versioned image from ECR, and overrides its command to run main.py " +
    "stage with exactly one source, one stage, and the run's run_id. That Python module reads its input from S3 " +
    "(often via the previous stage's own manifest), processes it, and writes its output back to S3 — the large " +
    "data itself never touches Step Functions or passes through any orchestration layer. What comes back is a " +
    "StageResult, written to S3 as a small JSON manifest at runs/<run_id>/<source>/<stage>.json — this is the " +
    "field names actually used in the code: written_paths is what the next stage reads to know exactly which " +
    "files changed. The container then exits and Step Functions moves to the next state. There's no persistent " +
    "compute anywhere in this design — no EC2, no Lambda, no long-running ECS service — and the network is " +
    "outbound-only: public subnet, public IP only for the few minutes the task runs, zero inbound rules, no NAT " +
    "Gateway, which is a deliberate cost trade-off documented in the architecture docs."
  );
};

module.exports = function (pres, H) {
  const { newSlide, title, card, bodyText, vArrow, shapeNode, C, ACCENT, SANS, MONO } = H;
  const slide = newSlide(pres, 5);
  const accent = ACCENT[5];

  title(slide, 5, "Merge to Main Deploys the Platform", { fontSize: 35 });

  // ---- Left: CI/CD sequence --------------------------------------------
  const lx = 0.4, lw = 6.35;
  let y = 1.4;
  const stepH = 0.4, stepGap = 0.14;

  function step(text, fill, opts) {
    const o = opts || {};
    shapeNode(slide, "roundRect", null, lx, y, lw, stepH, fill, { radius: 0.08, line: o.line || C.border, lineW: 0.75 });
    slide.addText(text, {
      x: lx + 0.18, y, w: lw - 0.36, h: stepH, isTextBox: true,
      fontFace: o.mono ? MONO : SANS, fontSize: 16, bold: !o.mono, color: C.text,
      valign: "middle", margin: 0,
    });
    const top = y;
    y += stepH + stepGap;
    vArrow(slide, lx + 0.3, top + stepH, y, {});
  }

  step("Pull Request → Code review", C.white);
  step("Merge to main", C.meta);
  step("GitHub Actions: Deploy workflow", C.aws);
  step("Configure AWS via OIDC · Verify identity", C.white, { mono: false });
  step("terraform init · fmt · validate · plan", C.white, { mono: true });
  step("Ensure ECR repository exists", C.white);
  step("Build & push image  →  tag sha-<12-char>", C.aws, { mono: false });
  step("Apply GitHubDeployRole policy", C.white);
  step("terraform apply  →  new ECS task revision", C.state, { mono: false });

  // last node — remove trailing arrow by drawing a terminator instead
  slide.addShape("roundRect", { x: lx, y, w: lw, h: stepH, fill: { color: C.success }, line: { color: C.border, width: 0.75 }, rectRadius: 0.08 });
  slide.addText("Resources created / updated", {
    x: lx + 0.18, y, w: lw - 0.36, h: stepH, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, valign: "middle", margin: 0,
  });

  // ---- Right: resource groups -------------------------------------------
  const rx = 7.05, rw = 5.85;
  const groups = [
    { name: "Delivery", items: "ECR · GitHub OIDC · Terraform backend", fill: C.aws },
    { name: "Runtime", items: "ECS · Fargate · Step Functions · Scheduler", fill: C.state },
    { name: "Storage + Logs", items: "S3 · Versioning · CloudWatch · Budget", fill: C.meta },
    { name: "Analytics + Network", items: "Glue · Athena · VPC · Public subnets · IGW", fill: C.eurostat },
  ];
  const gW = (rw - 0.2) / 2, gH = 1.15;
  groups.forEach((g, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const gx = rx + col * (gW + 0.2), gy = 1.4 + row * (gH + 0.16);
    card(slide, { x: gx, y: gy, w: gW, h: gH, fill: g.fill, line: "00000000", lineW: 0, radius: 0.1 });
    slide.addText(g.name, {
      x: gx + 0.16, y: gy + 0.1, w: gW - 0.32, h: 0.4, isTextBox: true,
      fontFace: SANS, fontSize: 18, bold: true, color: C.text, margin: 0,
    });
    slide.addText(g.items, {
      x: gx + 0.16, y: gy + 0.5, w: gW - 0.32, h: gH - 0.6, isTextBox: true,
      fontFace: SANS, fontSize: 16, color: "3F4E47", margin: 0, lineSpacingMultiple: 1.05,
    });
  });

  const cfgY = 1.4 + 2 * (gH + 0.16) + 0.14;
  slide.addText("Repository variables / secrets", {
    x: rx, y: cfgY, w: rw, h: 0.28, isTextBox: true,
    fontFace: SANS, fontSize: 16, bold: true, color: C.text, margin: 0,
  });
  slide.addText("AWS_REGION · AWS_ROLE_ARN · BUDGET_NOTIFICATION_EMAIL", {
    x: rx, y: cfgY + 0.36, w: rw, h: 0.3, isTextBox: true,
    fontFace: MONO, fontSize: 16, color: C.border, margin: 0,
  });

  const warnY = cfgY + 0.82;
  card(slide, { x: rx, y: warnY, w: rw, h: 0.7, fill: C.failure, line: "00000000", lineW: 0, radius: 0.1 });
  slide.addText("Deploy only — no pipeline data run", {
    x: rx + 0.2, y: warnY, w: rw - 0.4, h: 0.7, isTextBox: true,
    fontFace: SANS, fontSize: 18, bold: true, color: "6B2620", valign: "middle", margin: 0,
  });

  slide.addNotes(
    "This is the deploy.yml workflow, triggered on every push to main after a PR merges — never on a schedule, " +
    "and it never starts an actual pipeline run. GitHub Actions authenticates to AWS using OIDC — no long-lived " +
    "access keys stored anywhere. It runs terraform fmt/validate/plan for a preview, ensures the ECR repository " +
    "exists, builds and pushes the Docker image tagged by the 12-character short Git commit SHA, applies the " +
    "GitHub deploy role's own IAM policy first to avoid a permission race on IAM cleanup, then runs the real " +
    "terraform apply, which registers a new ECS task definition revision pointing at that exact image tag and " +
    "creates or updates every other AWS resource. It's important the audience understands the distinction: this " +
    "workflow only deploys infrastructure and code. Running Bootstrap, Historical, or Update — the pipelines " +
    "that actually download and process data — is a separate, manual step covered on the next few slides."
  );
};

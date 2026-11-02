// Two decks for AuxoCase: the business story for the CEO, the mechanics for the CTO.
const pptxgen = require("pptxgenjs");
const { applyTheme } = require("/Users/ashwinprajeeth/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/c9480283-6c08-4f2d-882d-0db3aea158b9/eae74383-72f3-4cf8-9ac4-385a20cb7a46/skills/pptx/scripts/apply_theme.js");

const OUT = "/Users/ashwinprajeeth/Code/AuxoCase/docs/decks/";
const BASE = "http://localhost:8002";

// The app's own palette: ink, gold, grey, with the good/bad greens and reds it uses.
const THEME = {
  name: "AuxoCase",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "201F1D", lt1: "FFFFFF", dk2: "605D5D", lt2: "EAE9E9",
    accent1: "B68235", accent2: "2D6A3E", accent3: "A1352A", accent4: "7D5411", accent5: "C9964A", accent6: "AEABA3",
    hlink: "B68235", folHlink: "605D5D",
  },
};
const H = THEME.colors; // hex, for the options that take hex only

const W = 13.33, HT = 7.5;
const LEFT = 0.6, TOP_BODY = 1.45;
const STORY_W = 4.5, VIS_X = 5.55, VIS_W = 7.2, BODY_H = 5.2;

function newDeck(title, subject) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  pres.title = title; pres.subject = subject; pres.author = "AuxoCase"; pres.company = "BrightPath Payroll";
  const C = pres.SchemeColor;
  const footer = (dark) => ({
    text: "AuxoCase for BrightPath Payroll · every figure is simulated on synthetic data", options: {
      x: LEFT, y: HT - 0.5, w: 9, h: 0.3, fontSize: 10, color: dark ? C.accent6 : C.text2, margin: 0, isTextBox: true,
    },
  });
  pres.defineSlideMaster({
    title: "LIGHT", background: { color: H.lt1 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: LEFT, y: 0.45, w: W - 2 * LEFT, h: 0.85, fontSize: 28, bold: true, color: C.text1, margin: 0, valign: "top", align: "left" } } },
      footer(false),
    ],
    slideNumber: { x: W - 1.2, y: HT - 0.5, w: 0.6, h: 0.3, fontSize: 10, color: H.dk2 },
  });
  pres.defineSlideMaster({
    title: "DARK", background: { color: H.dk1 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: LEFT, y: 2.3, w: W - 2 * LEFT, h: 1.6, fontSize: 40, bold: true, color: C.background1, margin: 0, valign: "bottom", align: "left" } } },
      { placeholder: { options: { name: "body", type: "body", x: LEFT, y: 4.05, w: W - 2 * LEFT, h: 1.4, fontSize: 20, color: C.accent5, margin: 0, valign: "top" } } },
      footer(true),
    ],
  });
  return { pres, C };
}

// ---------- building blocks ----------
function story(slide, C, paras, opts = {}) {
  // A left-hand narrative column: the first run bold as the lede, then the paragraphs.
  const runs = [];
  paras.forEach((p, i) => {
    const t = typeof p === "string" ? { text: p } : p;
    runs.push({ text: t.text, options: { bold: !!t.bold, color: t.color || C.text1, fontSize: t.size || 14, breakLine: i < paras.length - 1, paraSpaceAfter: 10, bullet: t.bullet ? true : undefined } });
  });
  slide.addText(runs, { x: opts.x ?? LEFT, y: opts.y ?? TOP_BODY, w: opts.w ?? STORY_W, h: opts.h ?? BODY_H, valign: "top", margin: 0, isTextBox: true, objectName: "Story" });
}

function caption(slide, C, text, x, y, w) {
  slide.addText(text, { x, y, w, h: 0.4, fontSize: 11, italic: true, color: C.text2, margin: 0, isTextBox: true, valign: "top", objectName: "Caption" });
}

function stat(slide, C, { x, y, w, h, big, label, sub, fill }) {
  slide.addShape("roundRect", { x, y, w, h, fill: { color: fill || C.background2 }, line: { color: fill || C.background2 }, rectRadius: 0.08, objectName: "Stat card" });
  slide.addText([
    { text: big, options: { fontSize: 34, bold: true, color: C.accent1, breakLine: true } },
    { text: label, options: { fontSize: 13, bold: true, color: C.text1, breakLine: !!sub } },
    ...(sub ? [{ text: sub, options: { fontSize: 11, color: C.text2 } }] : []),
  ], { x: x + 0.2, y: y + 0.12, w: w - 0.4, h: h - 0.24, valign: "top", margin: 0, isTextBox: true, objectName: "Stat text" });
}

function card(slide, C, { x, y, w, h, n, head, body }) {
  slide.addShape("roundRect", { x, y, w, h, fill: { color: C.background2 }, line: { color: C.background2 }, rectRadius: 0.08, objectName: "Card" });
  if (n !== undefined) {
    slide.addShape("ellipse", { x: x + 0.2, y: y + 0.2, w: 0.5, h: 0.5, fill: { color: C.accent1 }, line: { color: C.accent1 }, objectName: "Card number" });
    slide.addText(String(n), { x: x + 0.2, y: y + 0.2, w: 0.5, h: 0.5, fontSize: 16, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: "Card number text" });
  }
  const tx = n !== undefined ? x + 0.85 : x + 0.2;
  slide.addText([
    { text: head, options: { fontSize: 14, bold: true, color: C.text1, breakLine: true, paraSpaceAfter: 4 } },
    { text: body, options: { fontSize: 12, color: C.text1 } },
  ], { x: tx, y: y + 0.15, w: w - (tx - x) - 0.2, h: h - 0.3, valign: "top", margin: 0, isTextBox: true, objectName: "Card text" });
}

function table(slide, C, rows, { x, y, w, colW, fontSize = 12, rowH }) {
  const head = rows[0].map((t) => ({ text: t, options: { bold: true, color: H.lt1, fill: { color: H.dk1 }, fontSize, valign: "middle" } }));
  const body = rows.slice(1).map((r, i) => r.map((cell) => {
    const c = typeof cell === "object" && cell !== null && cell.text !== undefined ? cell : { text: String(cell) };
    return { text: c.text, options: Object.assign({ fontSize, color: H.dk1, fill: { color: i % 2 ? H.lt2 : H.lt1 }, valign: "middle" }, c.options || {}) };
  }));
  slide.addTable([head, ...body], { x, y, w, colW, rowH, border: { type: "solid", color: H.lt2, pt: 0.5 }, fontFace: THEME.bodyFontFace, margin: 0.06, objectName: "Table" });
}

const chartBase = (C) => ({
  chartColors: [H.accent1, H.accent6, H.accent2, H.accent3],
  catAxisLabelColor: H.dk2, valAxisLabelColor: H.dk2, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
  catAxisLabelFontSize: 11, valAxisLabelFontSize: 11,
  valGridLine: { color: H.lt2, size: 0.5 }, catGridLine: { style: "none" },
  dataLabelColor: H.dk1, dataLabelFontFace: "+mn-lt", dataLabelFontSize: 11,
  legendFontFace: "+mn-lt", legendFontSize: 11, legendColor: H.dk2,
  titleFontFace: "+mn-lt", titleFontSize: 13, titleColor: H.dk1,
});

function arrow(slide, C, x1, y1, x2, y2) {
  slide.addShape("line", { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color: C.text2, width: 1.5, endArrowType: "triangle" }, objectName: "Arrow" });
}
function box(slide, C, { x, y, w, h, text, fill, color, size = 12, bold = false, align = "center" }) {
  slide.addShape("roundRect", { x, y, w, h, fill: { color: fill || C.background2 }, line: { color: fill || C.background2 }, rectRadius: 0.06, objectName: "Box" });
  slide.addText(text, { x: x + 0.08, y: y + 0.05, w: w - 0.16, h: h - 0.1, fontSize: size, bold, color: color || C.text1, align, valign: "middle", margin: 0, isTextBox: true, objectName: "Box text" });
}

function shotsSlide(pres, C, section) {
  const slide = pres.addSlide({ masterName: "LIGHT", sectionTitle: section });
  slide.addText("See it running: one page a role, each a link to the demo", { placeholder: "title" });
  const shots = [
    ["manager", "/", "Sales manager · Overview", "Logged in as manager: the pool, leads reached in a week, every SDR's numbers; held queue, decisions and leadership in the sidebar."],
    ["marketing", "/model", "Marketing · Lead scoring", "Logged in as marketing: what to work on, the live model and its sign-off; How it decides and Leads to check beside it."],
    ["sdr", "/queue/900", "SDR · Alex Novak's queue", "Logged in as sdr1: one lead at a time with its odds, rank and reason, the drafted email and the call brief."],
  ];
  const iw = 3.9, ih = iw / 1.6, gap = 0.22;
  shots.forEach((t, i) => {
    const x = LEFT + i * (iw + gap), y = 1.55;
    slide.addImage({ path: OUT + "shots/" + t[0] + ".png", x, y, w: iw, h: ih, hyperlink: { url: BASE + t[1], tooltip: BASE + t[1] }, objectName: t[2] + " screenshot" });
    slide.addText([
      { text: t[2], options: { bold: true, fontSize: 14, color: H.accent4, hyperlink: { url: BASE + t[1] }, breakLine: true, paraSpaceAfter: 4 } },
      { text: t[3], options: { fontSize: 12, color: C.text1 } },
    ], { x, y: y + ih + 0.15, w: iw, h: 1.6, valign: "top", margin: 0, isTextBox: true, objectName: t[2] + " label" });
  });
  caption(slide, C, "Click a screenshot or its title to open the page on the machine running the service (localhost:8002); the accounts and password are on the login page. Every page is also listed on the next slide.", LEFT, 5.3, W - 2 * LEFT);
  return slide;
}


function costSlide(pres, C, section) {
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: section });
  s.addText("What it costs: about $65,000 to go live, then about $180 a month", { placeholder: "title" });
  story(s, C, [
    { text: "The software is built: 13,000 lines, 175 tests, a mock portal with HubSpot's semantics. The one-off cost is the work of putting it on BrightPath's portal and running it for a measured month, about 470 hours.", bold: true },
    "At $120 an hour for a senior engineer that is $52,000; with a 25% contingency for what a live portal turns up, about $65,000, well under the brief's $100,000 to $300,000. BrightPath's own time is the HubSpot admin, the sales manager and marketing for the setup and two hours of training an SDR.",
    "Running it is one small server and the model calls: about 10 million tokens a month, metered by the Claude CLI on the demo's 1,550 calls so far at the same per-call cost it reports to Revenium, scaled to 1,128 leads worked a month. Drafting on Opus is most of it; HubSpot needs no new seats and the enrichment tool is already licensed.",
    { text: "Model spend is about 12 cents a demo against $26 of SDR time and $4,800 a demo is worth. One month of the simulated gain ($2.3 M of first-year contract value) covers the build thirty times over; at a tenth of that gain, once.", color: C.text2, size: 12 },
  ]);
  table(s, C, [
    ["One-off: going live on BrightPath's portal", "Hours", "Cost"],
    ["Live HubSpot integration: properties, webhooks, batch writes, sequences, meetings, calendars checked against the portal", "120", "$14,400"],
    ["First model on three years of history, outcome fields mapped, enrichment tool and loss reasons connected", "100", "$12,000"],
    ["Hosting, secrets, backups, monitoring, security review", "40", "$4,800"],
    ["The SDRs' sequence, saved views or CRM card, onboarding and docs", "40", "$4,800"],
    ["The measured month: support, fixes, AI steps on one at a time, first retrain and sign-off", "80", "$9,600"],
    ["BrightPath's own time: HubSpot admin 40 h, manager and marketing 20 h, SDR training 24 h", "84", "$6,000"],
    [{ text: "Total, with a 25% contingency on the engineering", options: { bold: true } }, { text: "464", options: { bold: true } }, { text: "about $65,000", options: { bold: true } }],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [5.3, 0.7, 1.2], fontSize: 10.5, rowH: 0.28 });
  table(s, C, [
    ["Running, a month", "Model", "Calls", "Tokens a call", "Cost"],
    ["First email, follow-up, last email", "Opus", "2,800", "1,260", "$44"],
    ["Call briefs, demos booked, replies triaged, call notes", "Opus, Sonnet", "2,660", "1,500", "$26"],
    ["Signals, reasons and flags on scored leads", "Sonnet", "4,130", "850", "$13"],
    ["Verdicts, loss reasons; likely skips need no model", "Sonnet", "120", "1,000", "under $1"],
    ["Hosting, one small server (HubSpot seats and enrichment already paid)", "", "", "", "$100"],
    [{ text: "Total", options: { bold: true } }, "", { text: "9,700", options: { bold: true } }, { text: "~10 M a month", options: { bold: true } }, { text: "about $180", options: { bold: true } }],
  ], { x: VIS_X, y: TOP_BODY + 3.05, w: VIS_W, colW: [3.5, 1.0, 0.7, 1.0, 1.0], fontSize: 10.5, rowH: 0.28 });
  return s;
}


function appendixSlide(pres, C) {
  pres.addSection({ title: "Appendix" });
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Appendix" });
  s.addText("Appendix: HubSpot's points against AuxoCase's points table", { placeholder: "title" });
  const half = (W - 2 * LEFT - 0.4) / 2;
  s.addText("HubSpot's points model, as the brief describes it: title, size and pages, nothing for channel, nothing for a blank", { x: LEFT, y: 1.4, w: half, h: 0.5, fontSize: 13, bold: true, color: C.text1, margin: 0, isTextBox: true, valign: "top", objectName: "HubSpot heading" });
  table(s, C, [
    ["What HubSpot scores", "Points"],
    ["Job title: C-level", "30"], ["Job title: VP", "26"], ["Job title: Director", "20"], ["Job title: Manager", "12"], ["Job title: individual contributor", "3"],
    ["Job title left blank", "0"],
    ["Company size 50 to 500 employees", "18"], ["Company size 20 to 49 or 501 to 1,500", "10"], ["Company size outside that, or blank", "0"],
    ["Page views: 12 or more · 7 · 4 · 2", "25 · 18 · 10 · 5"], ["Viewed the pricing page", "10"],
    ["Channel (referral, webinar, event, website, email, paid ads)", "nothing"],
    [{ text: "MQL at 47 points or more", options: { bold: true } }, { text: "the gate", options: { bold: true } }],
  ], { x: LEFT, y: 1.95, w: half, colW: [half - 1.4, 1.4], fontSize: 10.5, rowH: 0.3 });
  const x2 = LEFT + half + 0.4;
  s.addText("AuxoCase's points table (points-v2): the cold start before a model is approved, and the baseline every model is measured against", { x: x2, y: 1.4, w: half, h: 0.5, fontSize: 13, bold: true, color: C.text1, margin: 0, isTextBox: true, valign: "top", objectName: "AuxoCase heading" });
  table(s, C, [
    ["What AuxoCase scores", "Points"],
    ["Channel: referral · webinar · events", "25 · 18 · 10"], ["Channel: website · email · paid ads", "8 · 5 · 4"],
    ["Job title: C-level · VP · Director", "15 · 14 · 12"], ["Job title: Manager · individual · blank", "8 · 2 · 0"],
    ["Company size 150 to 299", "15"], ["Company size 50 to 149 · 300 to 500", "12 · 12"], ["Company size over 500 · blank · under 50", "4 · 3 · 2"],
    ["Attended a webinar", "8"], ["Pricing page, a view (capped at 12)", "4"], ["Form submitted, each (capped at 9)", "3"], ["Page views, per 5 (capped at 6)", "1"],
    ["Competing payroll vendor in place", "−6"], ["Student or intern title", "−20"],
    [{ text: "Kept between 0 and 100; a blank is not a zero", options: { bold: true } }, ""],
  ], { x: x2, y: 1.95, w: half, colW: [half - 1.4, 1.4], fontSize: 10.5, rowH: 0.3 });
  caption(s, C, "Once a model is approved it replaces the table: a calibrated probability from 17 features, with Claude's reading of the notes capped at ±10 points. On the test leads the live model finds 23% more demos in its top K than HubSpot's points and about as many as this table; the table's edge over HubSpot is the channel row.", LEFT, 6.5, W - 2 * LEFT);
  return s;
}

function demoSlide(pres, C, section, extraRows) {
  shotsSlide(pres, C, section);
  const slide = pres.addSlide({ masterName: "LIGHT", sectionTitle: section });
  slide.addText("Every demo page, by role", { placeholder: "title" });
  story(slide, C, [
    { text: "The mock portal runs on this machine at localhost:8002. Every page is behind a login; the demo accounts and the password are listed on the login page.", bold: false },
    "Log in as manager to see everything, as marketing for the scoring pages, and as sdr1 (Alex Novak) for one SDR's queue. Logging in as another account replaces the session.",
    { text: "Each row links to the page. Open the deck on the machine running the service, or swap the host for the deployment's.", color: C.text2, size: 12 },
  ]);
  const link = (label, path) => ({ text: label, options: { hyperlink: { url: BASE + path }, color: H.accent4, underline: { style: "sng" } } });
  const rows = [
    ["Who", "Page", "What it shows"],
    ["Sales manager · manager", link("Overview", "/"), "Pool, leads reached in a week, every SDR's numbers, recent runs"],
    ["Sales manager", link("Held & overdue", "/held"), "Leads held with a reason, released by hand with a note to the SDR"],
    ["Sales manager", link("Decisions", "/decisions"), "Every worked, skipped, junk, approval and switch, by name"],
    ["Sales manager", link("Leadership", "/leadership"), "The shared number, where the gain comes from, capacity and its switches"],
    ["Marketing · marketing", link("Lead scoring", "/model"), "What to work on, the live model, the sign-off"],
    ["Marketing", link("How it decides", "/model?tab=decides"), "What the model adds, every version, calibration, the channel monitor"],
    ["Marketing", link("Leads to check", "/flagged"), "Records that disagree with their number, with the record's own words"],
    ["SDR · sdr1", link("Alex Novak's queue", "/queue/900"), "One waiting lead at a time, the reason, the drafted email, the call brief"],
    ...(extraRows || []),
  ];
  table(slide, C, rows, { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.7, 1.6, 3.9], fontSize: 12, rowH: 0.5 });
  slide.addNotes("Demo accounts: manager (Sam Okafor), marketing (Nadia Reyes), sdr1 to sdr12; the password is on the mock portal's login page.");
  return slide;
}

// =====================================================================
// Deck 1: the CEO
// =====================================================================
async function ceo() {
  const { pres, C } = newDeck("More demos from the same 12 SDRs", "AuxoCase business review");
  const months = ["Jul 25", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan 26", "Feb", "Mar", "Apr", "May", "Jun", "Aug 26", "Sep", "Oct", "Nov", "Dec", "Jan 27"];
  const demosBefore = [157, 174, 165, 160, 170, 176, 180, 185, 164, 178, 171, 186, 0, 0, 0, 0, 0, 0];
  const demosAfter = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 306, 304, 265, 622, 669, 629];

  pres.addSection({ title: "The result" });
  let s = pres.addSlide({ masterName: "DARK", sectionTitle: "The result" });
  s.addText("More demos from the same 12 SDRs", { placeholder: "title" });
  s.addText("AuxoCase: lead scoring, routing and a feedback loop inside HubSpot, built for BrightPath Payroll\nBusiness review · simulated results · November 2026", { placeholder: "body" });

  // 2 The problem
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The result" });
  s.addText("Today two thirds of qualified leads are never worked", { placeholder: "title" });
  story(s, C, [
    { text: "Marketing creates about 6,000 leads a month. HubSpot's points pass 1,500 of them to 12 SDRs, who can work 500 and choose by gut feel.", bold: true },
    "The median first response is three days, and a lead contacted inside a day books about twice as often. Good leads go cold while a thousand are never contacted.",
    "Partner referrals and webinar attendees close at two to three times the average, but their forms leave the title and company size blank, so HubSpot's points score them low.",
    { text: "The brief asked for more booked demos and faster follow-up without adding SDRs.", color: C.text2 },
  ]);
  const fx = VIS_X + 0.3;
  box(s, C, { x: fx, y: 1.6, w: 6.4, h: 0.9, text: "6,000 leads a month, from six channels", fill: C.accent6, color: C.text1, size: 16, bold: true });
  box(s, C, { x: fx + 0.8, y: 2.8, w: 4.8, h: 0.9, text: "1,500 pass HubSpot's MQL gate", fill: C.accent5, color: C.text1, size: 16, bold: true });
  box(s, C, { x: fx + 1.6, y: 4.0, w: 3.2, h: 0.9, text: "500 worked by the SDRs", fill: C.accent1, color: C.background1, size: 16, bold: true });
  arrow(s, C, fx + 3.2, 2.5, fx + 3.2, 2.8); arrow(s, C, fx + 3.2, 3.7, fx + 3.2, 4.0);
  s.addText([{ text: "1,000 MQLs a month never contacted", options: { bold: true, breakLine: true, fontSize: 14, color: C.accent3 } },
    { text: "Median first touch 3 days · 21% touched within a day", options: { fontSize: 12, color: C.text2 } }],
    { x: fx, y: 5.15, w: 6.4, h: 0.9, align: "center", valign: "top", margin: 0, isTextBox: true, objectName: "Funnel note" });

  // 3 The one number
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The result" });
  s.addText("One shared number: demos per 1,000 leads, up 278%", { placeholder: "title" });
  story(s, C, [
    { text: "Marketing and sales now read the same figure: how many demos a thousand leads turn into.", bold: true },
    "Before is twelve months of BrightPath-like history. After is the two months simulated ahead with the system in place and every AI step switched on, at the same 12 SDRs.",
    "The same team books 477 more demos a month: better picks, touched on their first day, and the time the AI steps save spent on leads that were never called.",
    { text: "Every figure in this deck is simulated on synthetic data, generated to match the brief and BrightPath's own answers. A real portal measures the same figures from what HubSpot records.", color: C.text2, size: 12 },
  ]);
  const cw = 3.45, ch = 1.9, gx = VIS_X, gy = TOP_BODY + 0.1;
  stat(s, C, { x: gx, y: gy, w: cw, h: ch, big: "28.6 → 108.2", label: "demos per 1,000 leads", sub: "+278%, same 12 SDRs" });
  stat(s, C, { x: gx + cw + 0.3, y: gy, w: cw, h: ch, big: "172 → 649", label: "demos booked a month", sub: "497 and 1,128 leads worked a month" });
  stat(s, C, { x: gx, y: gy + ch + 0.3, w: cw, h: ch, big: "70 h → 10 h", label: "median time to first touch", sub: "from the lead's arrival, not its assignment" });
  stat(s, C, { x: gx + cw + 0.3, y: gy + ch + 0.3, w: cw, h: ch, big: "21% → 82%", label: "touched within 24 hours", sub: "6,043 leads before, 4,862 routed" });

  // 4 Month by month
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The result" });
  s.addText("Every routed month beat every month before it", { placeholder: "title" });
  story(s, C, [
    { text: "History ran at 157 to 186 demos a month on about 500 leads worked, a demo rate of 32 to 37%.", bold: true },
    "The first three routed months kept the same 500 touches and booked 265 to 306, a demo rate of 52 to 62%, with the median first touch down from about 70 hours to 17.",
    "From November 2026 the AI steps are on: the same SDRs work about 1,100 leads a month and book 622 to 669, with nine in ten leads touched on their first day.",
    { text: "Grey: the SDRs chose by gut feel and HubSpot's score. Gold: ranked and routed by AuxoCase.", color: C.text2, size: 12 },
  ]);
  s.addChart("bar", [
    { name: "Before: gut feel and HubSpot's score", labels: months, values: demosBefore },
    { name: "After: ranked and routed by AuxoCase", labels: months, values: demosAfter },
  ], Object.assign(chartBase(C), {
    x: VIS_X, y: TOP_BODY, w: VIS_W, h: BODY_H, barDir: "col", barGrouping: "stacked", barGapWidthPct: 40,
    chartColors: [H.accent6, H.accent1], showLegend: true, legendPos: "b", showTitle: true, title: "Demos booked a month, by month of first touch",
    valAxisMinVal: 0, valAxisMaxVal: 700, valAxisMajorUnit: 100,
  }));

  // 5 Where the gain comes from
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The result" });
  s.addText("Where the extra 477 demos a month come from", { placeholder: "title" });
  story(s, C, [
    { text: "Better picks are worth about a fifth more demos. Speed is worth as much again. The AI steps are worth the rest.", bold: true },
    "Ranking alone, with three leads waiting at every desk, takes the month from 172 to 205. Discounting a lead past its first day adds 19. Cutting each desk to one waiting lead, refilled the moment it is touched, adds 50 more: the SDRs reach the best leads while they are fresh.",
    "Switching on the AI steps roughly halves the minutes a lead costs, and the same hours reach twice the leads: 649 demos a month.",
    { text: "Each bar adds one change to the one before it. One simulated month per setup, five seeds; the AI steps' bar is the months simulated ahead.", color: C.text2, size: 12 },
  ]);
  s.addChart("bar", [{ name: "Demos a month", labels: ["History", "Ranking, three waiting a desk  +33", "Freshness discount  +19", "Two waiting a desk  +25", "One waiting, refilled  +25", "The AI steps on  +375"], values: [172, 205, 224, 249, 274, 649] }],
    Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: VIS_W, h: BODY_H, barDir: "bar", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", showLegend: false, showTitle: true, title: "Demos a month, one change at a time", valAxisMinVal: 0, valAxisMaxVal: 750, catAxisLabelFontSize: 11 }));

  pres.addSection({ title: "The mechanism" });
  // 6 Speed
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The mechanism" });
  s.addText("Speed is the mechanism: a first-day lead books twice as often", { placeholder: "title" });
  story(s, C, [
    { text: "BrightPath told us a lead contacted within a day converts at roughly double the rate. The outcomes agree: 62% of leads touched on their first day booked, against 29% touched later.", bold: true },
    "So routing orders the pool by the chance of a demo times a freshness discount (a lead past its first day is worth half), and hands each SDR one waiting lead, refilled the moment they touch it.",
    "The median wait for a first touch fell from 70 hours to 10, and the share touched within a day rose from 21% to 82%.",
    { text: "On the mock the first-day effect is the generator's assumption from BrightPath's answer; on the real portal it is measured from the outcomes.", color: C.text2, size: 12 },
  ]);
  s.addChart("bar", [{ name: "Booked", labels: ["Touched within a day (5,259 leads)", "Touched later (5,652 leads)"], values: [62, 29] }],
    Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: 3.4, h: BODY_H, barDir: "col", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0\"%\"", showLegend: false, showTitle: true, title: "Share that booked a demo", valAxisMinVal: 0, valAxisMaxVal: 80, valAxisLabelFormatCode: "0\"%\"", catAxisLabelFontSize: 10 }));
  s.addChart("bar", [{ name: "Hours", labels: ["Before routing (6,043 leads)", "Routed by AuxoCase (4,862)"], values: [70, 10] }],
    Object.assign(chartBase(C), { x: VIS_X + 3.8, y: TOP_BODY, w: 3.4, h: BODY_H, barDir: "col", chartColors: [H.accent6], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0\" h\"", showLegend: false, showTitle: true, title: "Median hours to the first touch", valAxisMinVal: 0, valAxisMaxVal: 80, catAxisLabelFontSize: 10 }));

  // 7 Revenue
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The mechanism" });
  s.addText("What a demo is worth, and what the extra demos add up to", { placeholder: "title" });
  story(s, C, [
    { text: "A booked demo is worth about $4,800 in first-year contract value: the brief's $24,000 deal at a 20% close rate. The outcomes show 23% of the 3,434 demos old enough to have closed did.", bold: true },
    "At 172 demos a month the pipeline was worth about $826,000 a month in first-year value. At 649 it is $3.1 million: about $2.3 million a month more, or $27 million a year, from the same twelve people.",
    "Each demo costs about $26 of SDR time, and an SDR hour on a ranked lead books 1.7 demos.",
    { text: "Deal size and close rate are the brief's figures; the demo counts are simulated. Replace both with the portal's own once it has run.", color: C.text2, size: 12 },
  ]);
  table(s, C, [
    ["", "Before", "After", "Change"],
    ["Demos booked a month", "172", "649", "+477"],
    ["First-year value a demo", "$4,800", "$4,800", ""],
    ["First-year value a month", "$826,000", "$3,115,000", "+$2,290,000"],
    ["First-year value a year", "$9.9 M", "$37.4 M", "+$27.5 M"],
    ["Leads worked a month", "497", "1,128", "+631"],
    ["SDRs", "12", "12", "none added"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [2.6, 1.5, 1.6, 1.5], fontSize: 13, rowH: 0.42 });
  s.addChart("bar", [{ name: "First-year value a month, $", labels: ["Before", "After"], values: [826000, 3115000] }],
    Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY + 3.2, w: VIS_W, h: 2.0, barDir: "bar", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "$#,##0.00,,\"M\"", showLegend: false, showTitle: false, valAxisMinVal: 0, valAxisMaxVal: 4000000, valAxisLabelFormatCode: "$#,##0", valAxisHidden: true, valGridLine: { style: "none" } }));

  // 8 SDR time
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The mechanism" });
  s.addText("Half the minutes a lead, so the same hours reach twice the leads", { placeholder: "title" });
  story(s, C, [
    { text: "An SDR gives leads about 21 hours a month. A lead used to cost 30 minutes: a 20-minute cold touch, then a follow-up and a last email at 5 minutes each.", bold: true },
    "With the AI steps on, the follow-ups go out for the SDR and the notes, bookings and skips are handled, so a lead costs about 14.6 minutes. Capacity goes from 500 leads a month to 1,031, or 42 to 86 a desk.",
    "That is about 15 minutes saved on every lead: on the 1,128 leads worked a month, roughly 290 SDR hours, about 24 hours per SDR, all reinvested in leads nobody would have called.",
    { text: "The minutes a cold touch and the hours a month are read from the SDRs' own first touches in HubSpot once there are enough; until then they are the brief's figures.", color: C.text2, size: 12 },
  ]);
  s.addChart("bar", [
    { name: "Cold touch", labels: ["Before", "With the AI steps"], values: [20, 14.6] },
    { name: "Follow-up and last email", labels: ["Before", "With the AI steps"], values: [10, 0] },
  ], Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: 3.4, h: BODY_H, barDir: "col", barGrouping: "stacked", chartColors: [H.accent1, H.accent6], showValue: true, dataLabelPosition: "ctr", dataLabelFormatCode: "0.#;-0.#;", showLegend: true, legendPos: "b", showTitle: true, title: "SDR minutes a lead", valAxisMinVal: 0, valAxisMaxVal: 35 }));
  s.addChart("bar", [{ name: "Leads a month", labels: ["Before", "With the AI steps"], values: [500, 1031] }],
    Object.assign(chartBase(C), { x: VIS_X + 3.8, y: TOP_BODY, w: 3.4, h: BODY_H, barDir: "col", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "#,##0", showLegend: false, showTitle: true, title: "Leads the team can work a month", valAxisMinVal: 0, valAxisMaxVal: 1200 }));

  // 9 The AI steps
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The mechanism" });
  s.addText("Five AI steps take the chores, never the conversation", { placeholder: "title" });
  story(s, C, [
    { text: "Each step does one thing an SDR used to do by hand around a lead, and each is a switch the sales manager can turn off.", bold: true },
    "No AI contacts a prospect on its own: the first email waits in the SDR's HubSpot tasks for them to read and send, and every draft is checked by code before it is shown.",
    "The right-hand column is what the team's monthly capacity would fall to with that one step switched off, from 1,031 with all five on.",
  ]);
  table(s, C, [
    ["Step", "What it does for the SDR", "Capacity without it"],
    ["Follow-ups sent", "The follow-up and the last email go out 3 and 7 days on, drafted from the lead's activity that day, unless the lead answered", "514 leads a month"],
    ["Replies triaged", "Each reply is read as it lands: interested gets the answer drafted, not now is scheduled, not interested is closed", "a reply takes 4 minutes, not 10"],
    ["Call notes written up", "The fragments typed in HubSpot's call log become a summary, the company size, the next step as a task and the follow-up draft", "810 leads a month"],
    ["Likely skips held", "A lead of a kind the SDRs skip more often than not goes to the manager with the reason, before it costs the minutes", "935 leads a month"],
    ["Demos booked", "An interested reply gets three free times from the SDR's calendar; the pick is booked in HubSpot and confirmed", "850 leads a month"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.6, 4.0, 1.6], fontSize: 12, rowH: 0.8 });

  costSlide(pres, C, "The mechanism");

  pres.addSection({ title: "Trust" });
  // 10 Better picks
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Trust" });
  s.addText("The picks are better, and the number on a lead can be believed", { placeholder: "title" });
  story(s, C, [
    { text: "Of the leads the model put first and the SDRs worked, 60% booked a demo. Of the leads handed out at random, 20% did.", bold: true },
    "Against HubSpot's own points, the live model's top picks book 23% more demos on the same test leads.",
    "The score is a probability that holds: when the model says 55%, about 54% of those leads book. That is what lets marketing and sales argue about one number instead of two.",
    { text: "The random 8% is deliberate. It costs a few demos a month and is the only way to know whether the ranking is right rather than merely matching the SDRs' habits.", color: C.text2, size: 12 },
  ]);
  s.addChart("bar", [{ name: "Booked", labels: ["Ranked by AuxoCase (4,497 worked)", "Picked at random (235)", "Random, below HubSpot's gate (135)"], values: [60, 20, 27] }],
    Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: 3.5, h: BODY_H, barDir: "col", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0\"%\"", showLegend: false, showTitle: true, title: "Demo rate over six routed months", valAxisMinVal: 0, valAxisMaxVal: 70, valAxisLabelFormatCode: "0\"%\"", catAxisLabelFontSize: 10 }));
  s.addChart("line", [
    { name: "Share that booked", labels: ["26%", "36%", "46%", "55%", "64%", "74%"], values: [12, 34, 46, 54, 64, 77] },
    { name: "Exact", labels: ["26%", "36%", "46%", "55%", "64%", "74%"], values: [26, 36, 46, 55, 64, 74] },
  ], Object.assign(chartBase(C), { x: VIS_X + 3.8, y: TOP_BODY, w: 3.4, h: BODY_H, chartColors: [H.accent1, H.accent6], lineSize: 2, lineDataSymbolSize: 8, showLegend: true, legendPos: "b", showTitle: true, title: "What the score says, and what books", valAxisMinVal: 0, valAxisMaxVal: 100, valAxisLabelFormatCode: "0\"%\"", catAxisTitle: "the score on the lead", showCatAxisTitle: true, catAxisTitleFontSize: 10, catAxisTitleColor: H.dk2, catAxisTitleFontFace: "+mn-lt" }));

  // 11 Beyond the scores
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Trust" });
  s.addText("What the data says beyond the scores", { placeholder: "title" });
  story(s, C, [
    { text: "Most referral and webinar leads never reach the pool: 10% and 8% pass HubSpot's gate, against 49% from the website.", bold: true },
    "Half of them have no title or company size, and HubSpot's points give nothing for a blank. Letting the model rank every lead would add about 48 demos a month on the same 500 touches; it is built, switched off, waiting for outcomes from below the gate.",
    "Deals are lost mostly on price and to competitors. Larger companies leave for missing features: 29% of losses over 500 employees, about 12% under 300.",
    { text: "The enrichment tool, used consistently, filled a title or size on 539 of 748 pool leads that lacked one.", color: C.text2, size: 12 },
  ]);
  s.addChart("bar", [{ name: "Reach MQL", labels: ["Website", "Paid ads", "Email", "Events", "Referral", "Webinar"], values: [49, 24, 24, 17, 10, 8] }],
    Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: 3.5, h: BODY_H, barDir: "bar", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0\"%\"", showLegend: false, showTitle: true, title: "Share of each channel's leads that reach MQL", valAxisMinVal: 0, valAxisMaxVal: 60, valAxisLabelFormatCode: "0\"%\"" }));
  table(s, C, [
    ["Why deals were lost", "Under 300", "Over 500"],
    ["Price", "1,144", "21"],
    ["Competitor", "1,020", "28"],
    ["No decision", "839", "27"],
    ["Timing", "851", "37"],
    ["Missing feature", "634", "57"],
    ["Poor fit", "325", "11"],
    ["Other", "399", "12"],
  ], { x: VIS_X + 3.8, y: TOP_BODY, w: 3.4, colW: [1.7, 0.85, 0.85], fontSize: 11, rowH: 0.36 });
  caption(s, C, "Closed-lost reasons read from free text into fixed categories, by company size; 'under 300' sums the three bands below 300.", VIS_X + 3.8, TOP_BODY + 3.3, 3.4);

  // 12 Guardrails
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Trust" });
  s.addText("Sales keeps the final call on every lead", { placeholder: "title" });
  story(s, C, [
    { text: "Sales was nervous about hallucinations and unexplainable behaviour. The design answers that with rules code enforces, not with promises.", bold: true },
    "An SDR sees one number, a rank and a plain-English reason that quotes the record. They work, skip or junk every lead themselves, and every call is logged under their name and shown on the Decisions page.",
    "A new model goes live only when fixed rules both teams can read say it is better than the one running, with its numbers beside the live model's; the automatic sign-off can be switched off for two signatures.",
  ]);
  const gw = 3.45, gh = 2.35;
  card(s, C, { x: VIS_X, y: TOP_BODY, w: gw, h: gh, n: 1, head: "No AI contacts a prospect", body: "Every email waits in the SDR's HubSpot tasks to be read, edited and sent. The AI steps act only after the SDR's first touch, and the SDR can stop them from the lead page." });
  card(s, C, { x: VIS_X + gw + 0.3, y: TOP_BODY, w: gw, h: gh, n: 2, head: "The reader must quote the record", body: "Claude reads notes for buying signals and must copy its evidence word for word; code drops anything not in the record and caps the effect at 10 points. It never sets the score." });
  card(s, C, { x: VIS_X, y: TOP_BODY + gh + 0.3, w: gw, h: gh, n: 3, head: "Every decision has a name", body: "Who worked, skipped, released, approved or threw a switch is recorded from the login, never typed. Marketing resolves the leads the reader flags; the manager releases held leads with a note." });
  card(s, C, { x: VIS_X + gw + 0.3, y: TOP_BODY + gh + 0.3, w: gw, h: gh, n: 4, head: "Personal data stays in HubSpot", body: "The model uses role, company and behaviour, never names, emails or note text. Scores are written back to the contact; the service's own database holds no more than it needs to run." });

  pres.addSection({ title: "Next" });
  demoSlide(pres, C, "Next");

  // 14 Caveats and next steps
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Next" });
  s.addText("What the numbers do not show, and three months to go live", { placeholder: "title" });
  story(s, C, [
    { text: "Everything here is simulated. The history was generated to match the brief, and the first-day effect is BrightPath's own answer built into the generator, not something observed.", bold: true },
    "The real HubSpot client is written from the official documentation and has not yet run against a live portal. The capacity measurement rests on a handful of real touches.",
    "So the gain is a forecast with a credible mechanism, not a result. One month on BrightPath's own leads, with the exploration slice on, turns it into one.",
    { text: "Budget in the brief: $100,000 to $300,000; three months to go live inside HubSpot.", color: C.text2, size: 12 },
  ]);
  const tw = 2.2, ty = TOP_BODY + 0.3;
  [["Month 1", "Connect", "Create the auxo_* properties and the private app, point the webhook at the service, train the first model on three years of history, set the SDRs to one waiting lead each."],
   ["Month 2", "Measure", "Run on live leads with the 8% exploration slice on. Read the speed effect, the minutes a touch and the demo rate from HubSpot's own records; connect the enrichment tool."],
   ["Month 3", "Go live", "Switch the AI steps on one at a time, build the SDRs' sequence and saved views, approve the first retrained model, and read the shared number on the Leadership page."]].forEach((t, i) => {
    const x = VIS_X + i * (tw + 0.3);
    box(s, C, { x, y: ty, w: tw, h: 0.6, text: t[0] + " · " + t[1], fill: C.accent1, color: C.background1, size: 14, bold: true });
    s.addText(t[2], { x: x + 0.05, y: ty + 0.75, w: tw - 0.1, h: 3.4, fontSize: 12, color: C.text1, valign: "top", margin: 0, isTextBox: true, objectName: "Timeline text " + (i + 1) });
    if (i < 2) arrow(s, C, x + tw + 0.02, ty + 0.3, x + tw + 0.28, ty + 0.3);
  });

  appendixSlide(pres, C);

  // 15 Closing
  s = pres.addSlide({ masterName: "DARK", sectionTitle: "Next" });
  s.addText("The ask: one measured month on BrightPath's own leads", { placeholder: "title" });
  s.addText("Connect the portal, keep the exploration slice on, and let the Leadership page report the shared number from what HubSpot records.\nIf the simulated 477 extra demos a month hold even in part, the system pays for itself inside the first quarter.", { placeholder: "body" });

  const file = OUT + "AuxoCase-CEO-business-review.pptx";
  await pres.writeFile({ fileName: file });
  await applyTheme(file, THEME);
  console.log("wrote", file);
}

// =====================================================================
// Deck 2: the CTO
// =====================================================================
async function cto() {
  const { pres, C } = newDeck("How AuxoCase decides", "AuxoCase technical walkthrough");
  let s;

  pres.addSection({ title: "Shape" });
  s = pres.addSlide({ masterName: "DARK", sectionTitle: "Shape" });
  s.addText("How AuxoCase decides", { placeholder: "title" });
  s.addText("Scoring, routing and learning inside HubSpot: the mechanics behind the numbers\nTechnical walkthrough · November 2026", { placeholder: "body" });

  // 2 Three jobs
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Shape" });
  s.addText("One Python service, three jobs, HubSpot as the system of record", { placeholder: "title" });
  story(s, C, [
    { text: "FastAPI and Jinja2 for the pages, SQLite for the service's own state, scikit-learn for the model, httpx for HubSpot. One process; the jobs run on timers inside it.", bold: true },
    "In development a mock HubSpot in SQLite carries the real API's semantics: webhooks that duplicate and reorder, batch writes with per-record status, compare-and-set owners, property history. The real client is the same interface over the v3 API.",
    "Nothing lives outside HubSpot that an SDR needs: the score, rank, reason and routing are written to the contact, and the SDR works from HubSpot's tasks and sequences.",
    { text: "175 tests, offline, no model calls. CLI for every job: seed, sync, train, approve, score, route, monitor, simulate, eval, serve.", color: C.text2, size: 12 },
  ]);
  const ax = VIS_X + 0.2, aw = 6.8;
  box(s, C, { x: ax, y: 1.5, w: aw, h: 0.7, text: "HubSpot · contacts, companies, engagements, deals, owners, tasks, sequences", fill: C.accent6, size: 13, bold: true });
  const jobs = [["Job 1 · score", "enrich → features → calibrated p(demo) → reader (quotes, ±10) → reason → flag → rank → batch write auxo_*"],
    ["Job 2 · route", "pool of unowned MQLs ≤ 30 days → p × freshness → team's weekly reach, 8% random → round robin, compare-and-set owner, task → held queue, SLA"],
    ["Job 3 · learn", "outcomes → labels → channel p-chart → monthly retrain on a time split → verdict rules → sign-off → live, shadow, rescore"]];
  jobs.forEach((j, i) => {
    const y = 2.55 + i * 1.3;
    box(s, C, { x: ax, y, w: 1.6, h: 1.05, text: j[0], fill: C.accent1, color: C.background1, size: 13, bold: true });
    s.addText(j[1], { x: ax + 1.75, y, w: aw - 1.75, h: 1.05, fontSize: 11, color: C.text1, valign: "middle", margin: 0, isTextBox: true, objectName: "Job text " + (i + 1) });
    if (i < 2) arrow(s, C, ax + 0.8, y + 1.05, ax + 0.8, y + 1.3);
  });
  arrow(s, C, ax + 0.8, 2.2, ax + 0.8, 2.55);
  caption(s, C, "Webhook or polling in; auxo_* properties, owner, tasks and meetings out. The handoffs are Blueprint's three jobs, kept as drawn.", ax, 6.5, aw);

  // 3 HubSpot reads and writes
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Shape" });
  s.addText("What it reads from HubSpot, what it writes back, and how", { placeholder: "title" });
  story(s, C, [
    { text: "Every write is idempotent: scores are keyed by contact, model version and a hash of the feature row, so a retry or a replayed webhook writes nothing new.", bold: true },
    "Writes to auxo_* properties do not re-trigger the scorer. The owner is set with compare-and-set so two routing passes cannot assign the same lead twice, and an assignment counts only when both the owner and the task exist.",
    "Batch writes read the per-record status of a 207 reply and retry only the records that failed. With the real client, unsigned webhooks are refused.",
    { text: "The real client (hubspot/real.py) is written from the official docs and has not been run against a live portal; property internal names must be confirmed on the portal.", color: C.accent3, size: 12 },
  ]);
  table(s, C, [
    ["Direction", "What", "Mechanism"],
    ["Read", "Contact created, property changed", "Webhook subscription to /webhooks/hubspot, or search polling by last-modified"],
    ["Read", "Emails, calls, meetings, replies", "Engagements on the contact, polled every minute for the first touch and its outcome"],
    ["Read", "Deal stages, lead status, meetings", "Hourly outcome sync into booked / worked-no-demo / not-worked labels"],
    ["Write", "auxo_score, auxo_probability, auxo_rank, auxo_reason", "Batch property writes, keyed by contact + model + input hash"],
    ["Write", "auxo_review_flag, auxo_holdout, auxo_below_gate", "The reader's open flag kinds; the exploration and below-gate marks"],
    ["Write", "hubspot_owner_id, a task due in 4 h", "Compare-and-set on the owner; task and owner both required"],
    ["Write", "auxo_email_subject, auxo_email_body, sequence enrolment", "The drafted email as contact tokens; step one is a manual email task the SDR sends"],
    ["Write", "Meetings, closed lead status", "Demos booked for the SDR; replies that close the lead"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [0.9, 2.9, 3.4], fontSize: 11.5, rowH: 0.5 });

  pres.addSection({ title: "Job 1 · score" });
  // 4 Scoring pipeline
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 1 · score" });
  s.addText("Job 1: from a HubSpot record to one calibrated number", { placeholder: "title" });
  story(s, C, [
    { text: "Eight steps, each deterministic except the reader, and the reader is boxed in: it may add or remove at most 10 points and only with quotes code finds in the record.", bold: true },
    "Before a model exists the channel-aware points table scores every lead; it stays as the cold start and as the baseline every version is measured against.",
    "The reason an SDR sees is a template filled from the model's own per-feature contributions, so it is exact, and the counterfactual line says what would change the score most.",
    { text: "Scoring runs every 5 minutes on the change feed, 25 leads a batch; a new live model rescores the pool best-ranked first, within a time budget so the other jobs keep their minute.", color: C.text2, size: 12 },
  ]);
  const steps = [["1 Enrich", "a blank title or size asked once of the licensed tool, written back, logged"],
    ["2 Features", "channel, seniority, size band, industry, country; page views, forms, pricing views, webinar, referral, competitor"],
    ["3 Model", "calibrated p(demo) from the live logistic regression; the points table before one exists"],
    ["4 Reader", "Claude lists buying signals in the notes with exact quotes; code verifies each, caps the effect at ±10 points"],
    ["5 Explain", "per-feature contributions → fixed-wording reason codes → a counterfactual line"],
    ["6 Flag", "near the line, a large referral, text aimed at the scorer, the reader unsure → marketing's Leads to check"],
    ["7 Rank", "place in the current pool of unowned MQLs created in the last 30 days"],
    ["8 Write", "batch write of auxo_* properties, keyed by contact + model + input hash"]];
  steps.forEach((t, i) => {
    const col = i < 4 ? 0 : 1, row = i % 4;
    const x = VIS_X + col * 3.65, y = TOP_BODY + row * 1.3;
    box(s, C, { x, y, w: 1.1, h: 1.1, text: t[0], fill: C.accent1, color: C.background1, size: 12, bold: true });
    s.addText(t[1], { x: x + 1.2, y, w: 2.3, h: 1.1, fontSize: 10.5, color: C.text1, valign: "middle", margin: 0, isTextBox: true, objectName: "Step text " + (i + 1) });
  });

  // 5 Features
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 1 · score" });
  s.addText("The features: computable from the record, nothing personal", { placeholder: "title" });
  story(s, C, [
    { text: "Seventeen features, every one computable at scoring time from HubSpot fields alone, so any score can be reproduced from the record by its input hash.", bold: true },
    "Names, emails and note text are never features. Notes go only to the bounded reader. A blank is its own value: a missing title is 'unknown', not 'junior', which is what HubSpot's points get wrong for referrals and webinars.",
    "Each contribution maps to a fixed sentence, so the reason on the lead is the model's arithmetic in words, not a generated paragraph.",
  ]);
  table(s, C, [
    ["Kind", "Feature", "Reason wording, when it moves the score"],
    ["Categorical", "channel", "it came in as a partner or customer referral · through a webinar · through the website …"],
    ["Categorical", "seniority", "the contact is C-level · a director · an individual contributor · there is no job title on record"],
    ["Categorical", "size_band", "the company has 50 to 149 employees, inside the target band · the company size is unknown"],
    ["Categorical", "industry, country_group", "fit against BrightPath's customers"],
    ["Behaviour", "log_page_views, log_visits, num_form_submissions, pricing_page_views, email_clicks_30d", "it viewed the pricing page · it submitted two forms"],
    ["Behaviour", "webinar_registered, webinar_attended", "it registered for a webinar · it attended a webinar"],
    ["Fit", "is_referral, has_competitor, in_target_size", "the company already uses a competing payroll vendor · it is in the 50 to 500 employee band"],
    ["Hygiene", "is_student_or_intern, has_notes", "the job title suggests a student or intern"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.1, 2.6, 3.5], fontSize: 11.5, rowH: 0.52 });

  // 6 Training protocol
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 1 · score" });
  s.addText("Training protocol: tested once on unseen months, shipped as tested", { placeholder: "title" });
  story(s, C, [
    { text: "Regularised logistic regression by default, because its contributions are exact; a gradient-boosted challenger wins only if it beats it on the time split and stays calibrated.", bold: true },
    "Labels are three-state: booked, worked without a demo, not worked. Not worked is unlabelled, never a negative, and leads younger than the maturity window are excluded, not counted as misses.",
    "Fit on the oldest months of a 12-month window, choose the family and fit the Platt calibrator on the next two, report the newest two once. The model that goes live is the one measured; nothing is refitted.",
    { text: "Metrics at the capacity line K: precision@K and lift over HubSpot's points and over the points table, AUC-PR beside its base rate, Brier, expected calibration error. Never accuracy.", color: C.text2, size: 12 },
  ]);
  const ty2 = TOP_BODY + 0.4;
  box(s, C, { x: VIS_X, y: ty2, w: 3.6, h: 0.9, text: "Train · Oct 2025 – Apr 2026 (7 months)", fill: C.accent6, size: 12, bold: true });
  box(s, C, { x: VIS_X + 3.7, y: ty2, w: 1.7, h: 0.9, text: "Calibrate · May–Jun", fill: C.accent5, size: 12, bold: true });
  box(s, C, { x: VIS_X + 5.5, y: ty2, w: 1.7, h: 0.9, text: "Test · Jul–Aug", fill: C.accent1, color: C.background1, size: 12, bold: true });
  s.addText([
    { text: "demo-v6, the live model, on its own split", options: { bold: true, breakLine: true, fontSize: 12 } },
    { text: "Train: fit LR and HGB on the feature matrix.", options: { bullet: true, breakLine: true, fontSize: 12 } },
    { text: "Calibrate: pick the family on precision@K, fit the Platt calibrator that maps the ranking score to a probability; its version travels with the model's.", options: { bullet: true, breakLine: true, fontSize: 12 } },
    { text: "Test: 1,652 leads, 897 booked; precision 0.690 at K 481 against HubSpot's 0.559; ECE 0.014. Reported once, stored in the registry, shown beside the live model at sign-off.", options: { bullet: true, breakLine: true, fontSize: 12 } },
    { text: "Live: the pool is rescored best-ranked first; the previous model stays as a shadow for one cycle and its precision on recent labels is shown beside the live one.", options: { bullet: true, fontSize: 12 } },
  ], { x: VIS_X, y: ty2 + 1.2, w: VIS_W, h: 3.6, color: C.text1, valign: "top", margin: 0, isTextBox: true, paraSpaceAfter: 6, objectName: "Protocol text" });

  // 7 What the model adds
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 1 · score" });
  s.addText("What the model adds over the points tables, every version", { placeholder: "title" });
  story(s, C, [
    { text: "Against HubSpot's points, every version finds 13 to 26% more demos in its top K on the same test leads. Against the service's own channel-aware points table, the gap is close to nothing.", bold: true },
    "That is expected on worked leads: the SDRs' past choices hide the difference, because only leads someone chose to work have labels. On the generator's hidden truth, across every MQL, the model's 500 a month book 348 demos against 292 for HubSpot's points and 345 for the table.",
    "The exploration slice is what closes that gap on real data over time: 8% of slots go to leads the ranking did not choose.",
    { text: "Lift at K is precision@K divided by the baseline's precision on the same leads; K is capacity's share of each version's test leads.", color: C.text2, size: 12 },
  ]);
  const versions = ["demo-v1", "demo-v2", "demo-v3", "demo-v4", "demo-v5", "demo-v6 live", "demo-v7"];
  s.addChart("bar", [
    { name: "Over HubSpot's points", labels: versions, values: [1.196, 1.222, 1.155, 1.126, 1.249, 1.234, 1.260] },
    { name: "Over the cold-start points table", labels: versions, values: [1.008, 0.928, 0.991, 0.945, 0.964, 0.988, 1.034] },
  ], Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: VIS_W, h: BODY_H, barDir: "col", chartColors: [H.accent1, H.accent6], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.00", showLegend: true, legendPos: "b", showTitle: true, title: "Lift at K: the model's precision over each table's, same test leads", valAxisMinVal: 0.8, valAxisMaxVal: 1.4, valAxisMajorUnit: 0.1 }));

  // 8 Live vs previous
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 1 · score" });
  s.addText("Live against the rest: the table the sign-off page compares", { placeholder: "title" });
  story(s, C, [
    { text: "This is the table marketing signs from. Lifts and calibration error compare across columns; precision only within one, since each version has its own test months and K.", bold: true },
    "demo-v6 went live by the automatic sign-off: better lift over HubSpot than demo-v1, calibration within a point of it, more labelled rows. demo-v7 ranks best of all but its numbers are off by 12.7 points, so the rules sent it back whatever its lift.",
    { text: "Six candidates were each trained after one more simulated month; the months are the generator's clock.", color: C.text2, size: 12 },
  ]);
  table(s, C, [
    ["Measure", "demo-v6 · live", "demo-v1 · previous live", "demo-v7 · sent back"],
    ["Trained", "2 Oct 2026", "3 May 2026", "1 Nov 2026"],
    ["Trained on, then calibrated on", "Oct 2025–Apr 2026 · May–Jun", "not recorded", "Nov 2025–May 2026 · Jun–Jul"],
    ["Tested on · leads · booked", "Jul–Aug 2026 · 1,652 · 897", "Feb–Mar 2026 · 1,000 · 358", "Aug–Sep 2026 · 2,228 · 1,277"],
    ["Precision at K", "0.690 at K 481", "0.448 at K 299", "0.736 at K 626"],
    ["HubSpot's points · points table", "0.559 · 0.699", "0.375 · 0.445", "0.585 · 0.713"],
    ["Lift over HubSpot's points", "1.234×", "1.196×", "1.260×"],
    ["Lift over the points table", "0.988×", "1.008×", "1.034×"],
    ["Calibration error (ECE)", "off by 1.4 points (0.014)", "off by 0.7 points (0.007)", { text: "off by 12.7 points (0.127)", options: { color: H.accent3, bold: true } }],
    ["AUC-PR (base rate)", "0.677 (0.543)", "0.446 (0.358)", "0.706 (0.573)"],
    ["Labelled rows · booked", "5,595 · 2,550", "5,404 · 1,880", "6,248 · 3,029"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [2.3, 1.7, 1.6, 1.6], fontSize: 11, rowH: 0.42 });

  // 9 Calibration
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 1 · score" });
  s.addText("Calibration: when demo-v6 says 55%, about 54% book", { placeholder: "title" });
  story(s, C, [
    { text: "The score on a lead is a probability, and on the test months it holds: across the bands with enough leads the gap between what the model said and what booked is a point or two.", bold: true },
    "Expected calibration error is 0.014 over 1,652 test leads. Bands under 30 leads are shown hollow on the page and read loosely; here they are left out.",
    "This is the property that makes the number shared: a 55 means the same thing to marketing planning a campaign and to an SDR choosing what to call next.",
    { text: "A candidate whose calibration is more than a point worse than the live model's is sent back by the rules, whatever its ranking: an SDR cannot act on a number that lies.", color: C.text2, size: 12 },
  ]);
  s.addChart("line", [
    { name: "Share that booked", labels: ["26%", "36%", "46%", "55%", "64%", "74%"], values: [12, 34, 46, 54, 64, 77] },
    { name: "Exact (what the score says)", labels: ["26%", "36%", "46%", "55%", "64%", "74%"], values: [26, 36, 46, 55, 64, 74] },
  ], Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: 3.7, h: BODY_H, chartColors: [H.accent1, H.accent6], lineSize: 2, lineDataSymbolSize: 8, showLegend: true, legendPos: "b", showTitle: true, title: "demo-v6 on Jul–Aug 2026, bands of 30 or more leads", valAxisMinVal: 0, valAxisMaxVal: 100, valAxisLabelFormatCode: "0\"%\"", catAxisTitle: "average score in the band", showCatAxisTitle: true, catAxisTitleFontSize: 10, catAxisTitleColor: H.dk2, catAxisTitleFontFace: "+mn-lt" }));
  table(s, C, [
    ["Score band", "Leads", "Average score", "Booked"],
    ["20–30%", "33", "26%", "12%"], ["30–40%", "89", "36%", "34%"], ["40–50%", "382", "46%", "46%"],
    ["50–60%", "595", "55%", "54%"], ["60–70%", "387", "64%", "64%"], ["70–80%", "137", "74%", "77%"],
    ["80–90%", "15 · few", "82%", "93%"],
  ], { x: VIS_X + 4.0, y: TOP_BODY, w: 3.2, colW: [0.9, 0.8, 0.8, 0.7], fontSize: 11, rowH: 0.36 });

  pres.addSection({ title: "Job 2 · route" });
  // 10 Routing
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 2 · route" });
  s.addText("Job 2: routing within capacity, ordered by value now", { placeholder: "title" });
  story(s, C, [
    { text: "Fixed rules, no AI. The pool is the unowned MQLs created in the last 30 days, ordered by p(demo) × 0.5 once a lead is past its first day.", bold: true },
    "The team holds at most its reach in untouched leads: a week's touches at its monthly capacity, handed out round robin, skipping full desks. 8% of slots go to a lead drawn at random from below the line, marked auxo_holdout. The first email or call logged in HubSpot is the first touch; within a minute the slot is refilled.",
    "A task due in 4 hours alerts the SDR; no touch in 24 hours brings a reminder. A strong lead unplaced for 24 hours, or one the skips model says the SDRs would skip, goes to the manager's held queue with the reason.",
    { text: "Runs every 5 minutes, one lead at a time under a write lock, owner set by compare-and-set.", color: C.text2, size: 12 },
  ]);
  table(s, C, [
    ["Freshness discount", "Waiting leads a desk", "Touched within 24 h", "Demos a month"],
    ["off", "3", "15% (13–17%)", "205 (196–215)"],
    ["on", "3", "31% (29–33%)", "224 (214–235)"],
    ["on", "2", "49% (49–50%)", "249 (236–259)"],
    ["on", "1, refilled on touch", "64% (61–66%)", "274 (267–280)"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.7, 1.9, 1.8, 1.8], fontSize: 12, rowH: 0.42 });
  caption(s, C, "python run.py queue-limits --seeds 5: one simulated month per setup at the brief's capacity, seeds 101 to 105, range in brackets. History booked 172.", VIS_X, TOP_BODY + 2.35, VIS_W);
  s.addChart("bar", [{ name: "Demos", labels: ["No discount, 3 a desk", "Discount, 3 a desk", "Discount, 2 a desk", "Discount, 1 a desk"], values: [205, 224, 249, 274] }],
    Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY + 2.9, w: VIS_W, h: 2.3, barDir: "bar", chartColors: [H.accent1], showValue: true, dataLabelPosition: "outEnd", showLegend: false, showTitle: false, valAxisMinVal: 150, valAxisMaxVal: 300, valAxisHidden: true, valGridLine: { style: "none" } }));

  // 11 Capacity
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 2 · route" });
  s.addText("Capacity is measured from the SDRs' own touches, not typed", { placeholder: "title" });
  story(s, C, [
    { text: "Leads a month = SDRs × hours a month ÷ minutes a lead. Minutes a lead = the cold touch, less what the AI steps take off it, plus two follow-ups unless they are sent for the SDR.", bold: true },
    "Two inputs are read from HubSpot: the minutes a cold touch is the median gap between one first touch and the next inside a working session (a gap over 45 minutes ends a session), and the hours a month is the time in those sessions scaled to a month.",
    "Each replaces its default once the evidence is there: 30 gaps across 2 SDRs, or 10 days with a touch for 2 SDRs. A figure typed on the Leadership page wins over both; typing the measured one back drops the typed one.",
    { text: "The capacity line, the Overview, the simulator and the cost report all read the result, so one change moves every page.", color: C.text2, size: 12 },
  ]);
  table(s, C, [
    ["Input", "Default", "Now", "Source"],
    ["Minutes a cold touch", "20", "20 (measured so far: 17.5)", "assumed until 2 SDRs have 30 gaps; 11 touches by 3 SDRs so far"],
    ["Minutes a follow-up, SDR sends it", "5 × 2", "0", "follow-ups sent for the SDR"],
    ["Minutes logging a call", "4", "1", "call notes written up"],
    ["Minutes booking a demo", "8", "1", "demos booked for the SDR"],
    ["Minutes a skip", "3", "held instead", "likely skips held for the manager"],
    ["Hours an SDR gives leads a month", "20.8", "20.8", "assumed until 2 SDRs have 10 active days"],
    [{ text: "Leads a month", options: { bold: true } }, { text: "500", options: { bold: true } }, { text: "1,031 (86 a desk)", options: { bold: true } }, "12 × 20.8 h ÷ 14.55 min"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [2.2, 0.9, 1.7, 2.4], fontSize: 11, rowH: 0.5 });
  caption(s, C, "The call-log, booking and skip savings apply on the share of touches that are calls, that book, or that would have been skipped, so the net is 14.55 minutes a lead on the demo data.", VIS_X, TOP_BODY + 4.5, VIS_W);

  pres.addSection({ title: "Job 3 · learn" });
  // 12 Learning loop
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 3 · learn" });
  s.addText("Job 3: outcomes flow back and the monitor says when to retrain", { placeholder: "title" });
  story(s, C, [
    { text: "Every hour meetings, deal stages and lead status become labels. Every day a p-chart per channel sets the observed demo rate against the average score of the leads worked, with limits three standard errors either side; under 30 worked leads a month is held, not judged.", bold: true },
    "Faster follow-up changed the base rate: leads booked more because they were touched sooner, so January 2027 is above prediction on five of six channels. That is the alarm; the fix is a retrain, not a channel weight.",
    "Every 30 days a candidate is trained on the sliding window of mature labels and appears on the Lead scoring page beside the live model.",
    { text: "The close model trains only on post-demo rows and is reported separately; closed-won is never the demo model's target.", color: C.text2, size: 12 },
  ]);
  const chans = ["Website", "Paid ads", "Email", "Events", "Referral", "Webinar"];
  s.addChart("bar", [
    { name: "Observed demo rate", labels: chans, values: [57.4, 49.0, 53.7, 51.2, 79.2, 82.7] },
    { name: "Average score (predicted)", labels: chans, values: [37.1, 34.0, 35.7, 39.8, 48.9, 48.0] },
  ], Object.assign(chartBase(C), { x: VIS_X, y: TOP_BODY, w: VIS_W, h: 3.3, barDir: "col", chartColors: [H.accent1, H.accent6], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0", showLegend: true, legendPos: "b", showTitle: true, title: "Channel monitor, January 2027: worked leads in the mock portal, %", valAxisMinVal: 0, valAxisMaxVal: 100, valAxisLabelFormatCode: "0\"%\"" }));
  table(s, C, [
    ["Channel", "Worked", "Demos", "Flag"],
    ["Website", "605", "347", "above prediction, retrain due"], ["Paid ads", "145", "71", "above"], ["Email", "82", "44", "above"],
    ["Events", "86", "44", "inside limits"], ["Referral", "48", "38", "above"], ["Webinar", "75", "62", "above"],
  ], { x: VIS_X, y: TOP_BODY + 3.45, w: VIS_W, colW: [1.4, 1.0, 1.0, 3.8], fontSize: 10, rowH: 0.25 });

  // 13 Verdict rules
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Job 3 · learn" });
  s.addText("The sign-off is fixed rules; the reader only puts them into words", { placeholder: "title" });
  story(s, C, [
    { text: "A manager who does not know ML should not read seven columns of decimals to sign a model off. Code compares the candidate with the live model on three things they can feel and writes one line.", bold: true },
    "The rules run at every page load, so a verdict is never missing. Claude words the reasons in one sentence; code keeps it only if it starts with the verdict, stays under 60 words, uses no modelling word and cites no figure the facts do not hold. Otherwise the rules' own wording stands.",
    "Automatic sign-off, on unless switched off by name: each run sends back what the rules send back, promotes the approvable candidate whose picks book the most demos, and sends the rest back as not the best of the round. The rows are ordinary approvals, so Decisions shows them like a person's.",
  ]);
  table(s, C, [
    ["Rule, against the live model on each one's own test leads", "Verdict"],
    ["Calibration error more than one point worse than the live model's", "Send back, whatever the lift: the number on a lead could not be believed"],
    ["Lift over HubSpot's points better by more than 0.005 and at least as many labelled rows", "Approve"],
    ["Lift no better, or fewer labelled rows", "Send back: a reshuffle of every queue would buy nothing"],
    ["Test results not recorded", "Send back: it cannot be judged"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [4.3, 2.9], fontSize: 11, rowH: 0.5 });
  s.addShape("roundRect", { x: VIS_X, y: TOP_BODY + 2.85, w: VIS_W, h: 2.3, fill: { color: C.background2 }, line: { color: C.background2 }, rectRadius: 0.08, objectName: "Verdict example" });
  s.addText([
    { text: "The line under demo-v6's sign-off, worded from the rules' facts", options: { bold: true, fontSize: 12, breakLine: true, paraSpaceAfter: 6 } },
    { text: "“Approve: its top picks book 23 percent more demos than HubSpot's list would, where demo-v1's 20 percent; when it says a lead is 55 percent, about 54 percent of those leads book, so the number on a lead can be believed; and it learned from more worked leads, 5,595 against 5,404.”", options: { fontSize: 12, italic: true, breakLine: true, paraSpaceAfter: 6 } },
    { text: "Checked: starts with the verdict · 52 words · no 'lift', 'calibration', 'model' or 'rows' · every figure (23, 20, 55, 54, 5,595, 5,404) is one of the facts.", options: { fontSize: 11, color: C.text2 } },
  ], { x: VIS_X + 0.2, y: TOP_BODY + 2.95, w: VIS_W - 0.4, h: 2.1, color: C.text1, valign: "top", margin: 0, isTextBox: true, objectName: "Verdict example text" });

  pres.addSection({ title: "The reader" });
  // 14 Where Claude is used
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The reader" });
  s.addText("Where Claude is used, and what code checks before anyone sees it", { placeholder: "title" });
  story(s, C, [
    { text: "The model never asks the reader for a number. Every generative step reads text, must quote the record, and is checked by code; a malformed answer falls back to the deterministic path.", bold: true },
    "Three modes behind one interface: AUXO_LLM=cli runs the Claude Code CLI headlessly, api the Anthropic API, and the default offline a scripted reader, so the whole system, the tests and the demo run without a model.",
    "Each call is metered, so the cost report prices a demo with the reader's own spend in it (about 2 cents a draft at the defaults).",
  ]);
  table(s, C, [
    ["Reader", "Reads", "Produces", "Code's check"],
    ["Signals", "the lead's notes", "buying signals with exact quotes", "each quote found verbatim; effect capped at ±10 points"],
    ["Reason", "the model's contributions", "a plain-English rewrite", "every fact in the rewrite is in the contributions"],
    ["Emails, call brief", "the record and the score's drivers", "first email, follow-ups, opener, questions, voicemail", "template fallback; checked for facts, length, no promises"],
    ["Replies", "a reply as HubSpot logs it", "kind + one-line summary; the answer drafted", "fixed kinds; out of office never counts as an answer"],
    ["Call notes", "fragments in the call log", "summary, size, next step, follow-up", "facts must come from the fragments"],
    ["Scheduling", "an interested reply", "three times offered; the named time booked", "the time must parse; a taken slot gets new times"],
    ["Loss reasons", "free-text closed-lost reason", "one of seven categories, with a quote", "category dropped if the quote is not in the text"],
    ["Flags", "the record of a lead next out", "a flag kind from a fixed list, with evidence", "evidence verbatim in the record; never changes a score"],
    ["Verdict", "the rules' reasons and facts", "one sentence", "verdict word first, < 60 words, no jargon, only the facts' figures"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.2, 1.7, 2.1, 2.2], fontSize: 11, rowH: 0.48 });

  // 15 The five AI steps as code
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "The reader" });
  s.addText("The five AI steps, as code: a switch, a trigger, an action, a check", { placeholder: "title" });
  story(s, C, [
    { text: "Each step is a setting the sales manager throws on the Leadership page, recorded by name, and a module under routing/ with its reader under llm/.", bold: true },
    "All five act only after the SDR's first touch and stop the moment the lead answers or books, or when the SDR stops them from the lead page. On the mock the sends are logged; on a real portal the sequence's own steps send from the rewritten contact tokens.",
    "Each takes minutes off the capacity formula only on the share of touches it applies to, so the saving on the Leadership page is what the SDRs' own mix of calls, bookings and skips earns.",
  ]);
  table(s, C, [
    ["Switch", "Module", "Trigger", "Action", "Minutes"],
    ["followups_auto", "routing/followups.py", "first email was the first touch", "follow-up at +3 days, last email at +7, drafted from that day's activity, sent from the SDR", "10 → 0 a lead"],
    ["replies_auto", "routing/replies.py, llm/replies.py", "a reply lands on an owned lead", "interested or a question: answer drafted, lead first in queue; not now: date recorded; not interested, wrong person, unsubscribe: closed", "10 → 4 a reply"],
    ["call_notes_auto", "routing/callnotes.py, llm/callnotes.py", "a call logged with notes", "summary and facts on the lead, size on the company, next step as a task, follow-up drafted", "4 → 1 a call"],
    ["skips_auto", "routing/skips.py", "every routing pass", "skip and junk decisions counted by trait, smoothed; a trait skipped clearly above the rest holds the lead with the reason", "3 saved a skip"],
    ["scheduling_auto", "routing/scheduling.py, llm/scheduling.py", "an interested reply", "three free times from the SDR's calendar offered; a named time booked in HubSpot, confirmed, follow-ups stopped", "8 → 1 a booking"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.2, 1.5, 1.3, 2.3, 0.9], fontSize: 11, rowH: 0.85 });

  costSlide(pres, C, "The reader");

  pres.addSection({ title: "Operations" });
  // 16 Roles & security
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Operations" });
  s.addText("Who may do what, and what keeps the pages and writes safe", { placeholder: "title" });
  story(s, C, [
    { text: "Three roles. The role on the session decides the sidebar and what each route allows; who approved, released or threw a switch is recorded from the session, never typed.", bold: true },
    "Passwords are scrypt hashes; five wrong tries lock the account for fifteen minutes. Sessions are HttpOnly cookies stored as hashes, idle twelve hours at most.",
    "Only the host names in AUXO_ALLOWED_HOSTS are served; a write from another site's origin is refused; every response carries nosniff, no-framing and a content security policy that allows scripts from this service only. Forms that act on a lead or a model carry the state the person saw, so a stale edit is refused and explained.",
  ]);
  table(s, C, [
    ["Role", "Sees", "May"],
    ["SDR", "their own queue and its leads", "work, skip, junk, nudge a rank; send the checked drafts; stop the follow-ups"],
    ["Sales manager", "overview, every queue, held & overdue, decisions, leadership, lead scoring, leads to check", "everything: set capacity, throw the switches, run jobs, manage staff, release held leads with a note, sign off for either team"],
    ["Marketing", "lead scoring, how it decides, leads to check", "approve or send back for marketing, resolve flags, switch the automatic sign-off off"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.3, 2.6, 3.3], fontSize: 11 });
  const sy = TOP_BODY + 2.6, sw = 2.3;
  [["Webhooks", "Signed by HubSpot's app secret with the real client; unsigned ones refused. The mock's duplicated and reordered events are handled the same way."],
   ["Personal data", "Business contacts only. No names, emails or note text in the features; the service's database keeps the scores and decisions, HubSpot keeps the record."],
   ["Least privilege", "The private app gets the contact, engagement, task, sequence and meeting scopes it writes with and nothing wider; the MCP server, if used, is read-only."]].forEach((t, i) => {
    card(s, C, { x: VIS_X + i * (sw + 0.15), y: sy, w: sw, h: 2.5, head: t[0], body: t[1] });
  });

  // 17 Scheduler
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Operations" });
  s.addText("The jobs on timers inside the server", { placeholder: "title" });
  story(s, C, [
    { text: "run.py serve runs the jobs on timers in the same process; each run is one row in the Overview's recent runs, and a failed job never stops the others.", bold: true },
    "Training only produces a candidate; it goes live through the sign-off. Rescoring after a promotion stops after 45 seconds a run so the other jobs keep their minute, and resumes best-ranked first.",
    "AUXO_SCHEDULER=off turns the timers off and AUXO_EVERY_<JOB>_MIN changes an interval; every job is also a CLI command and a /jobs/* endpoint, so a HubSpot or n8n workflow can call it.",
    { text: "On the demo data the service clock is frozen, so leads never age into new holds or reminders there; run.py rebase moves the dates onto the present and lets the clock run.", color: C.text2, size: 12 },
  ]);
  table(s, C, [
    ["Job", "Every", "What it does"],
    ["score", "5 min", "scores new or changed leads from the change feed, 25 a batch, writes auxo_*"],
    ["rescore", "1 min", "after a model goes live, rescores the pool best-ranked first, 45 s a run"],
    ["route", "5 min", "fills the team's reach, round robin, 8% exploration; holds and reminders"],
    ["touches", "1 min", "reads first touches, call outcomes and replies from the contacts' activity"],
    ["outreach", "1 min", "drafts the first email for new assignments, enrols the sequence"],
    ["followups", "5 min", "sends the follow-up and last email on their day, when switched on"],
    ["flags", "10 min", "marketing's leads to check; the verdict sentences; the automatic sign-off"],
    ["remind", "60 min", "re-alerts assigned leads with no first touch by the 24 h SLA"],
    ["sync", "60 min", "meetings, deal stages and lead status into outcome labels"],
    ["monitor", "daily", "the per-channel p-chart of observed against predicted demo rate"],
    ["train", "30 days", "a candidate model on the sliding window of mature labels"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [1.2, 0.9, 5.1], fontSize: 11.5, rowH: 0.4 });

  demoSlide(pres, C, "Operations", [["Any role", { text: "/jobs/*", options: { color: H.accent4 } }, "The jobs as endpoints, for a HubSpot or n8n workflow; /webhooks/hubspot takes the subscription"]]);

  // 19 Known gaps and go-live
  s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Operations" });
  s.addText("Known gaps, and the seven steps to a live portal", { placeholder: "title" });
  story(s, C, [
    { text: "What is not yet proven, stated plainly.", bold: true },
    { text: "The real HubSpot client has never run against a live portal; property internal names vary by portal and must be confirmed.", bullet: true },
    { text: "Every evaluation figure is on synthetic data; the first-day effect is the generator's assumption from BrightPath's answer 4.", bullet: true },
    { text: "The measured capacity rests on 11 first touches by 3 SDRs; the defaults stay in force until the thresholds are met.", bullet: true },
    { text: "On worked leads the model barely beats the channel-aware points table; the exploration slice must run for months before that gap can be read on real data.", bullet: true },
    { text: "Scoring below the MQL gate and the automated sequence below the capacity line are built and off; both wait for outcomes there.", bullet: true },
  ]);
  table(s, C, [
    ["Step", "Going live in HubSpot"],
    ["1", "Create the auxo_* contact properties (ensure_properties) and a private app with the scopes in docs/ARCHITECTURE.md"],
    ["2", "Set HUBSPOT_PRIVATE_APP_TOKEN; point a webhook subscription at /webhooks/hubspot with HUBSPOT_APP_SECRET, or poll by last-modified; add the host to AUXO_ALLOWED_HOSTS"],
    ["3", "Confirm property internal names against GET /crm/v3/properties/contacts on the portal"],
    ["4", "Train the first model on the three years of history, mature labels only, exploration slice on from day one; set stale_lead_factor from the measured speed effect; one waiting lead a desk"],
    ["5", "Connect the enrichment tool behind the Enricher interface (AUXO_ENRICH); run losses on the closed-lost history"],
    ["6", "Create the SDRs' sequence: step one a manual email task from the auxo_email tokens; later steps automated from the same tokens; set AUXO_SEQUENCE_ID and connect each SDR's inbox"],
    ["7", "Put the queue in a HubSpot CRM card or a saved view sorted by auxo_rank with auxo_reason as a column; the service's pages do the same standalone"],
  ], { x: VIS_X, y: TOP_BODY, w: VIS_W, colW: [0.6, 6.6], fontSize: 11.5, rowH: 0.6 });

  appendixSlide(pres, C);

  s = pres.addSlide({ masterName: "DARK", sectionTitle: "Operations" });
  s.addText("Deterministic where it decides, generative only where it reads", { placeholder: "title" });
  s.addText("A calibrated model ranks, fixed rules route and sign off, code checks every sentence the reader writes, and HubSpot stays the record.\nNext: a live portal, one measured month, and the first retrain on BrightPath's own outcomes.", { placeholder: "body" });

  const file = OUT + "AuxoCase-CTO-technical-walkthrough.pptx";
  await pres.writeFile({ fileName: file });
  await applyTheme(file, THEME);
  console.log("wrote", file);
}

(async () => { await ceo(); await cto(); })().catch((e) => { console.error(e); process.exit(1); });

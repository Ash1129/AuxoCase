// Screenshots of the three role pages on the local mock portal, one login each.
const puppeteer = require("puppeteer-core");
const BASE = "http://localhost:8002";
const PAGES = [
  ["manager", "manager", "/"],
  ["marketing", "marketing", "/model"],
  ["sdr", "sdr1", "/queue/900"],
];
const OUT = "/Users/ashwinprajeeth/Code/AuxoCase/docs/decks/shots/";
(async () => {
  const browser = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
  for (const [name, user, path] of PAGES) {
    const ctx = await browser.createBrowserContext();
    const page = await ctx.newPage();
    await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
    await page.goto(BASE + "/login", { waitUntil: "networkidle0" });
    await page.type("#username", user);
    await page.type("#password", process.env.DEMO_PW);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("form button[type=submit], form input[type=submit], form button")]);
    await page.goto(BASE + path, { waitUntil: "networkidle0" });
    await page.screenshot({ path: OUT + name + ".png" });
    console.log(name, page.url());
    await ctx.close();
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });

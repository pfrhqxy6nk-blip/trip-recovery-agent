import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const landingUrl = process.env.LANDING_URL || "http://127.0.0.1:5173/";
const outputDir = process.env.VISUAL_OUTPUT_DIR || "/private/tmp/trip-watch-qa";

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });

try {
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await desktop.goto(landingUrl);
  await desktop.waitForLoadState("networkidle");
  await desktop.screenshot({ path: `${outputDir}/desktop-hero.png` });
  await desktop.locator("#plan").scrollIntoViewIfNeeded();
  await desktop.screenshot({ path: `${outputDir}/desktop-plan.png` });
  await desktop.locator("#watch").scrollIntoViewIfNeeded();
  await desktop.screenshot({ path: `${outputDir}/desktop-watch.png` });
  await desktop.locator("#flight").scrollIntoViewIfNeeded();
  await desktop.screenshot({ path: `${outputDir}/desktop-film.png` });
  await desktop.locator(".final-cta").scrollIntoViewIfNeeded();
  await desktop.screenshot({ path: `${outputDir}/desktop-arrival.png` });

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await mobile.goto(landingUrl);
  await mobile.waitForLoadState("networkidle");
  await mobile.screenshot({ path: `${outputDir}/mobile-hero.png` });
  await mobile.locator(".final-cta").scrollIntoViewIfNeeded();
  await mobile.screenshot({ path: `${outputDir}/mobile-arrival.png` });
} finally {
  await browser.close();
}

console.log(`Visual QA captures written to ${outputDir}`);

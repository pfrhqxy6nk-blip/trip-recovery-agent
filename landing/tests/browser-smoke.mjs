import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright";

const landingUrl = process.env.LANDING_URL || "http://127.0.0.1:5173/";

function collectHealth(page) {
  const consoleErrors = [];
  const assetFailures = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    if (new URL(request.url()).pathname.startsWith("/assets/")) {
      assetFailures.push(`${request.method()} ${request.url()} · ${request.failure()?.errorText || "failed"}`);
    }
  });
  page.on("response", (response) => {
    if (new URL(response.url()).pathname.startsWith("/assets/") && response.status() >= 400) {
      assetFailures.push(`${response.status()} ${response.url()}`);
    }
  });
  return { consoleErrors, assetFailures };
}

async function settle(page) {
  await page.waitForLoadState("domcontentloaded");
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(150);
}

function assertHealthy(health) {
  assert.deepEqual(health.consoleErrors, [], "the page must not emit console errors");
  assert.deepEqual(health.assetFailures, [], "all local visual assets must load");
}

async function assertNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    wideElements: [...document.querySelectorAll("body *")]
      // Full-bleed cinematic layers intentionally overscan by a few pixels so
      // their scaled edge never reveals a seam. The document itself must still
      // remain within the viewport; exclude only those decorative layers from
      // the DOM-box assertion.
      .filter((element) => !element.matches(".film-frame, .planning-frame, .footer-landscape"))
      .filter((element) => element.getBoundingClientRect().right > window.innerWidth + 1)
      .slice(0, 5)
      .map((element) => element.className || element.tagName),
  }));
  assert.ok(
    overflow.documentWidth <= overflow.viewportWidth + 1,
    `mobile page overflows horizontally: ${JSON.stringify(overflow)}`,
  );
  assert.deepEqual(overflow.wideElements, []);
}

test("desktop landing story, assets and Telegram recovery interaction are healthy", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const health = collectHealth(page);
    await page.goto(landingUrl);
    await settle(page);

    assert.equal(await page.title(), "Trip Watch — Your travel agent in Telegram");
    await page.locator("h1").waitFor({ state: "visible", timeout: 5000 });
    const rootText = await page.locator("#root").innerText();
    assert.match(rootText, /Your travel agent\s+lives in Telegram\./);
    assert.match(rootText, /Tell us where you want to go\./);
    assert.match(rootText, /Paris · 6 nights · €600/);
    assert.match(rootText, /Trip Watch turns a disruption into a resolved plan/);
    assert.match(rootText, /One forward\.\s+A living itinerary\./);
    assert.match(rootText, /PDF ticket/);
    assert.match(rootText, /The agent acts first\./);
    assert.match(rootText, /Open Trip Watch in Telegram/);
    for (const selector of ["#top", "#plan", "#watch", ".route-section", "#import", "#flight", "#compensation", "#rules", ".final-cta"]) {
      assert.equal(await page.locator(selector).count(), 1, `${selector} chapter must exist`);
    }

    await page.locator(".approve-button").click();
    await page.getByText("Trip recovered.").waitFor({ state: "visible" });
    await page.locator(".reset-button").click();
    await page.locator(".approve-button").waitFor({ state: "visible" });

    const assets = await page.locator("img").evaluateAll((images) => images.map((image) => ({
      src: image.getAttribute("src"), width: image.naturalWidth,
    })));
    for (const asset of [
      "paper-route-v2-white.png", "compensation-claim-v1.png", "autonomy-flow-v1.png",
      "trip-watch-footer-landscape-v1.png", "cinematic-sky-01.png", "cinematic-sky-02.png",
      "cinematic-sky-03.png", "flight-film-05.png", "flight-film-06.png", "flight-film-07.png",
      "import-film-01.png", "import-film-04.png", "planning-film-01.png", "planning-film-02.png",
      "planning-film-03.png",
    ]) {
      assert.ok(assets.some((item) => item.src?.includes(asset) && item.width > 0), `${asset} must load`);
    }
    assert.equal(await page.locator(".source-film > img").count(), 4);
    assert.equal(await page.locator('a[href="https://t.me/tripagentai_bot"]').count(), 6);

    const framelessVisuals = await page.evaluate(() => ({
      compensationBackground: getComputedStyle(document.querySelector(".compensation-section .paper-visual")).backgroundColor,
      compensationPadding: getComputedStyle(document.querySelector(".compensation-section .paper-visual")).padding,
      importShadow: getComputedStyle(document.querySelector(".source-film")).boxShadow,
    }));
    assert.match(framelessVisuals.compensationBackground, /rgba?\(0,\s*0,\s*0,\s*0\)|transparent/);
    assert.equal(framelessVisuals.compensationPadding, "0px");
    assert.equal(framelessVisuals.importShadow, "none");

    const heroGeometry = await page.locator(".hero").evaluate((hero) => {
      const screen = hero.querySelector(".hero-screen-wrap");
      const heroBox = hero.getBoundingClientRect();
      const screenBox = screen.getBoundingClientRect();
      return { heroHeight: hero.offsetHeight, screenBottom: screenBox.bottom - heroBox.top };
    });
    assert.ok(heroGeometry.heroHeight >= heroGeometry.screenBottom, "Telegram scene must remain inside hero");

    await page.locator(".planning-film").scrollIntoViewIfNeeded();
    await page.evaluate(() => {
      const section = document.querySelector(".planning-film");
      window.scrollTo(0, section.offsetTop + window.innerHeight * 1.35);
    });
    await page.waitForTimeout(200);
    assert.match(await page.locator(".planning-frame.active").getAttribute("src"), /planning-film-0[2-3]\.png/);
    assert.match(await page.locator(".planning-copy h2").innerText(), /Choices become|One clear plan/);

    await page.evaluate(() => window.scrollTo(0, 900));
    await page.waitForTimeout(100);
    const frameBlend = await page.locator(".hero").evaluate((hero) => [
      "--sky-one-opacity", "--sky-two-opacity", "--sky-three-opacity",
    ].map((property) => Number.parseFloat(getComputedStyle(hero).getPropertyValue(property))));
    assert.ok(frameBlend.every(Number.isFinite), "cinematic sky frames must interpolate on scroll");

    await page.locator(".route-cinematic").scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    const routeSignal = await page.locator(".route-disruption").evaluate((node) => ({
      opacity: Number.parseFloat(getComputedStyle(node).opacity), animation: getComputedStyle(node).animationName,
    }));
    assert.ok(routeSignal.opacity > 0.9, "route disruption signal should be visible");
    assert.match(routeSignal.animation, /route-signal|route-pulse/);

    await page.locator(".flight-film").scrollIntoViewIfNeeded();
    await page.evaluate(() => {
      const film = document.querySelector(".flight-film");
      window.scrollTo(0, film.offsetTop + window.innerHeight * 1.5);
    });
    await page.waitForTimeout(200);
    assert.match(await page.locator(".film-frame.active").getAttribute("src"), /flight-film-06\.png|flight-film-07\.png/);
    assert.match(await page.locator(".film-copy h2").innerText(), /One delay|The safe work is done/);

    await page.locator(".multimodal-section").scrollIntoViewIfNeeded();
    await page.evaluate(() => {
      const section = document.querySelector(".multimodal-section");
      window.scrollTo(0, section.offsetTop + window.innerHeight * .8);
    });
    await page.waitForTimeout(200);
    assert.match(await page.locator(".source-film > img.active").getAttribute("src"), /import-film-0[2-4]\.png/);
    assertHealthy(health);
  } finally {
    await browser.close();
  }
});

test("mobile navigation and layout stay inside the viewport", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const health = collectHealth(page);
    await page.goto(landingUrl);
    await settle(page);
    await page.locator(".menu-toggle").click();
    assert.equal(await page.locator(".nav-links.open").count(), 1);
    await assertNoHorizontalOverflow(page);
    await page.locator(".approve-button").click();
    await page.getByText("Trip recovered.").waitFor({ state: "visible" });
    assertHealthy(health);
  } finally {
    await browser.close();
  }
});

test("reduced motion keeps the final flight frame readable without a sticky scene", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    const page = await context.newPage();
    const health = collectHealth(page);
    await page.goto(landingUrl);
    await settle(page);
    const state = await page.evaluate(() => {
      const film = document.querySelector(".flight-film");
      return {
        position: getComputedStyle(film.querySelector(".film-sticky")).position,
        height: film.offsetHeight,
        activeFrame: film.querySelector(".film-frame.active")?.getAttribute("src"),
        copy: film.querySelector(".film-copy")?.innerText,
      };
    });
    assert.equal(state.position, "relative");
    assert.ok(state.height < 900 * 2.5, `reduced-motion scene should not be a long sticky scroll: ${state.height}`);
    assert.match(state.activeFrame, /flight-film-07\.png/);
    assert.match(state.copy, /safe work|decision/i);
    assertHealthy(health);
    await context.close();
  } finally {
    await browser.close();
  }
});

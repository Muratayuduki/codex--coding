import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";

const LOAD_TIMEOUT_MS = 15000;
const ACTION_SETTLE_MS = 150;
const VIEWPORTS = new Map([
  ["desktop", { width: 1280, height: 800 }],
  ["mobile", { width: 390, height: 844 }],
]);
const KEYS = ["Tab", "Enter", "Escape", "ArrowDown", "ArrowUp", "Space", "Backspace"];
const INPUT_VALUES = [
  "hello",
  "UX monkey test",
  "長い入力".repeat(100),
  "<script>alert('xss')</script>",
  "😀🚀✨",
  "' OR '1'='1",
];

function fail(message) {
  console.error(`[ux-monkey-test:monkey] ${message}`);
  process.exit(1);
}

function parseArgs(argv) {
  const parsed = new Map();
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      fail(`Unexpected argument: ${token}`);
    }
    const key = token.slice(2);
    if (key.length === 0) {
      fail("Empty option name is invalid");
    }
    if (parsed.has(key)) {
      fail(`Duplicate option: --${key}`);
    }
    const valueIndex = i + 1;
    if (valueIndex >= argv.length || argv[valueIndex].startsWith("--")) {
      fail(`Missing value for --${key}`);
    }
    parsed.set(key, argv[valueIndex]);
    i = valueIndex;
  }
  return parsed;
}

function requiredArg(args, key) {
  if (!args.has(key)) {
    fail(`Missing required option: --${key}`);
  }
  return args.get(key);
}

function optionalArg(args, key, defaultValue) {
  if (args.has(key)) {
    return args.get(key);
  }
  return defaultValue;
}

function parsePositiveInteger(value, key) {
  if (!/^[1-9][0-9]*$/u.test(value)) {
    fail(`--${key} must be a positive integer: ${value}`);
  }
  return Number.parseInt(value, 10);
}

function parseSeed(value) {
  if (!/^-?[0-9]+$/u.test(value)) {
    fail(`--seed must be an integer: ${value}`);
  }
  return Number.parseInt(value, 10);
}

function parseHttpUrl(rawUrl, label) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch (error) {
    fail(`${label} is not a valid URL: ${rawUrl}`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    fail(`${label} must use http or https: ${rawUrl}`);
  }
  return parsed;
}

function assertViewport(name) {
  if (!VIEWPORTS.has(name)) {
    fail(`--viewport must be desktop or mobile: ${name}`);
  }
  return VIEWPORTS.get(name);
}

function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function randomInt(rng, maxExclusive) {
  return Math.floor(rng() * maxExclusive);
}

function choose(rng, values) {
  return values[randomInt(rng, values.length)];
}

function serializeError(error) {
  if (error instanceof Error) {
    return { message: error.message, stack: error.stack };
  }
  return { message: String(error) };
}

async function evaluateTargets(page) {
  return page.evaluate(() => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const visible =
        typeof element.checkVisibility === "function"
          ? element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
          : style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
      return visible && rect.width > 0 && rect.height > 0;
    }

    function receivesPointerEvents(element) {
      let current = element;
      while (current instanceof Element) {
        if (window.getComputedStyle(current).pointerEvents === "none") {
          return false;
        }
        current = current.parentElement;
      }
      return true;
    }

    function cssPath(element) {
      if (element.id) {
        return `#${CSS.escape(element.id)}`;
      }
      const parts = [];
      let current = element;
      while (current instanceof Element && current !== document.documentElement) {
        const tag = current.tagName.toLowerCase();
        const parent = current.parentElement;
        if (parent === null) {
          parts.push(tag);
          break;
        }
        const sameTagSiblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName);
        const index = sameTagSiblings.indexOf(current) + 1;
        parts.push(`${tag}:nth-of-type(${index})`);
        current = parent;
      }
      return parts.reverse().join(" > ");
    }

    const clickableSelectors = 'a[href],button,[role="button"],[onclick]';
    const inputSelectors = 'input:not([type="hidden"]),textarea,[contenteditable="true"]';
    const clickables = Array.from(document.querySelectorAll(clickableSelectors))
      .filter((element) => isVisible(element) && receivesPointerEvents(element))
      .map((element) => ({
        selector: cssPath(element),
        text: (element.textContent ?? element.getAttribute("aria-label") ?? "").replace(/\s+/gu, " ").trim(),
      }));
    const inputs = Array.from(document.querySelectorAll(inputSelectors))
      .filter((element) => isVisible(element) && !element.disabled && !element.readOnly)
      .map((element) => ({
        selector: cssPath(element),
        tagName: element.tagName.toLowerCase(),
      }));

    return { clickables, inputs };
  });
}

function isTransientNavigationError(error) {
  const message = serializeError(error).message;
  return (
    message.includes("Execution context was destroyed") ||
    message.includes("Cannot find context with specified id") ||
    message.includes("Navigation failed because page was closed") ||
    message.includes("Timeout")
  );
}

async function waitForStablePage(page) {
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    try {
      await page.waitForLoadState("domcontentloaded", { timeout: Math.min(1000, remaining) });
      await page.evaluate(() => true);
      return;
    } catch (error) {
      if (!isTransientNavigationError(error)) {
        throw error;
      }
      lastError = error;
      await page.waitForTimeout(100);
    }
  }
  throw new Error(`Timed out waiting for page to become stable: ${serializeError(lastError).message}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const startUrl = parseHttpUrl(requiredArg(args, "url"), "--url");
  const outDir = requiredArg(args, "out");
  const events = parsePositiveInteger(optionalArg(args, "events", "300"), "events");
  const seed = parseSeed(optionalArg(args, "seed", "42"));
  const viewportName = optionalArg(args, "viewport", "desktop");
  const viewport = assertViewport(viewportName);
  const storageState = args.has("storage-state") ? args.get("storage-state") : undefined;

  await fs.mkdir(path.join(outDir, "screenshots"), { recursive: true });
  const actionsPath = path.join(outDir, "actions.jsonl");
  const errorsPath = path.join(outDir, "errors.json");
  await fs.writeFile(actionsPath, "");

  const browser = await chromium.launch({ headless: true });
  const contextOptions = { viewport, acceptDownloads: false };
  if (storageState !== undefined) {
    contextOptions.storageState = storageState;
  }
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  page.setDefaultTimeout(LOAD_TIMEOUT_MS);
  page.setDefaultNavigationTimeout(LOAD_TIMEOUT_MS);

  const rng = mulberry32(seed);
  const actions = [];
  const errors = [];
  const visitedScreens = new Set();
  let errorSequence = 0;
  let errorQueue = Promise.resolve();

  async function recordError(entry) {
    await page.screenshot({ path: path.join(outDir, entry.screenshot), fullPage: true });
    errors.push(entry);
  }

  function queueError(error, url) {
    const index = errorSequence + 1;
    errorSequence = index;
    const screenshot = path.join("screenshots", `error-${index}.png`);
    const entry = {
      error,
      url,
      precedingActions: actions.slice(-5),
      screenshot,
    };
    errorQueue = errorQueue.then(() => recordError(entry));
  }

  async function drainErrorQueue() {
    await errorQueue;
  }

  page.on("console", (message) => {
    if (message.type() === "error") {
      queueError({ type: "console.error", message: message.text() }, page.url());
    }
  });
  page.on("pageerror", (error) => {
    queueError({ type: "pageerror", message: error.message, stack: error.stack }, page.url());
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure();
    queueError(
      {
        type: "requestfailed",
        message: failure === null ? "request failed" : failure.errorText,
      },
      request.url(),
    );
  });
  page.on("response", (response) => {
    const status = response.status();
    if (status >= 400) {
      queueError(
        {
          type: "http",
          message: `HTTP ${status} ${response.statusText()}`,
        },
        response.url(),
      );
    }
  });
  page.on("dialog", (dialog) => {
    void dialog.dismiss();
  });
  page.on("popup", (popup) => {
    void popup.close();
  });
  page.on("download", (download) => {
    void download.cancel();
  });

  let response;
  try {
    response = await page.goto(startUrl.toString(), { waitUntil: "domcontentloaded", timeout: LOAD_TIMEOUT_MS });
  } catch (error) {
    throw new Error(`Start URL is unreachable: ${startUrl.toString()}. ${serializeError(error).message}`);
  }
  if (response !== null && response.status() >= 400) {
    throw new Error(`Start URL returned HTTP ${response.status()}: ${startUrl.toString()}`);
  }
  visitedScreens.add(page.url());

  async function appendAction(action) {
    actions.push(action);
    await fs.appendFile(actionsPath, `${JSON.stringify(action)}\n`);
  }

  async function enforceOrigin() {
    const current = new URL(page.url());
    if (current.origin === startUrl.origin) {
      return;
    }
    let returnedToHistory = false;
    const responseFromHistory = await page.goBack({ waitUntil: "domcontentloaded", timeout: LOAD_TIMEOUT_MS });
    returnedToHistory = responseFromHistory !== null && new URL(page.url()).origin === startUrl.origin;
    if (responseFromHistory === null && new URL(page.url()).origin !== startUrl.origin) {
      await page.goto(startUrl.toString(), { waitUntil: "domcontentloaded", timeout: LOAD_TIMEOUT_MS });
      returnedToHistory = new URL(page.url()).origin === startUrl.origin;
    }
    if (!returnedToHistory) {
      throw new Error(`Failed to return to start origin after navigation guard: ${page.url()}`);
    }
  }

  for (let n = 1; n <= events; n += 1) {
    await waitForStablePage(page);
    const targets = await evaluateTargets(page);
    const roll = rng();
    const timestamp = new Date().toISOString();
    let type;
    let selector = null;
    let value = null;

    if (roll < 0.4) {
      type = "click";
      if (targets.clickables.length > 0) {
        const target = choose(rng, targets.clickables);
        selector = target.selector;
        await page.locator(selector).click({ timeout: 3000, noWaitAfter: true });
      } else {
        value = "no-visible-click-target";
      }
    } else if (roll < 0.5) {
      type = "burst-click";
      const clickCount = 5 + randomInt(rng, 6);
      value = `count=${clickCount}`;
      if (targets.clickables.length > 0) {
        const target = choose(rng, targets.clickables);
        selector = target.selector;
        const box = await page.locator(selector).boundingBox({ timeout: 3000 });
        if (box === null) {
          throw new Error(`Selected burst-click target has no bounding box: ${selector}`);
        }
        const x = box.x + box.width / 2;
        const y = box.y + box.height / 2;
        for (let i = 0; i < clickCount; i += 1) {
          await page.mouse.click(x, y);
          await page.waitForTimeout(20);
        }
      } else {
        value = "no-visible-click-target";
      }
    } else if (roll < 0.7) {
      type = "input";
      if (targets.inputs.length > 0) {
        const target = choose(rng, targets.inputs);
        selector = target.selector;
        value = choose(rng, INPUT_VALUES);
        await page.locator(selector).fill(value, { timeout: 3000 });
      } else {
        value = "no-visible-input-target";
      }
    } else if (roll < 0.8) {
      type = "key";
      value = choose(rng, KEYS);
      await page.keyboard.press(value);
    } else if (roll < 0.9) {
      type = "history";
      value = rng() < 0.5 ? "back" : "forward";
      const historyResponse =
        value === "back"
          ? await page.goBack({ waitUntil: "domcontentloaded", timeout: LOAD_TIMEOUT_MS })
          : await page.goForward({ waitUntil: "domcontentloaded", timeout: LOAD_TIMEOUT_MS });
      if (historyResponse === null) {
        value = `${value}; no-history-entry`;
      }
    } else {
      type = "scroll";
      const deltaX = randomInt(rng, viewport.width) - Math.floor(viewport.width / 2);
      const deltaY = randomInt(rng, viewport.height * 2) - Math.floor(viewport.height / 2);
      value = `x=${deltaX},y=${deltaY}`;
      await page.mouse.wheel(deltaX, deltaY);
    }

    await page.waitForTimeout(ACTION_SETTLE_MS);
    await waitForStablePage(page);
    await enforceOrigin();
    await waitForStablePage(page);
    await drainErrorQueue();
    visitedScreens.add(page.url());
    await appendAction({ n, type, selector, value, url: page.url(), timestamp });
  }

  await errorQueue;
  await fs.writeFile(errorsPath, `${JSON.stringify(errors, null, 2)}\n`);
  await browser.close();

  console.log(
    JSON.stringify(
      {
        actions: events,
        visitedScreens: visitedScreens.size,
        errors: errors.length,
        seed,
        events,
        viewport: viewportName,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  fail(serializeError(error).message);
});

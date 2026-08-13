import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";

const LOAD_TIMEOUT_MS = 15000;
const SETTLE_MS = 300;
const VIEWPORTS = new Map([
  ["desktop", { width: 1280, height: 800 }],
  ["mobile", { width: 390, height: 844 }],
]);
const DOWNLOAD_EXTENSIONS = new Set([
  ".7z",
  ".csv",
  ".doc",
  ".docx",
  ".gz",
  ".pdf",
  ".tar",
  ".tgz",
  ".xls",
  ".xlsx",
  ".zip",
]);

function fail(message) {
  console.error(`[ux-monkey-test:crawl] ${message}`);
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
  if (!/^[1-9][0-9]*$/.test(value)) {
    fail(`--${key} must be a positive integer: ${value}`);
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

function normalizeUrl(rawUrl) {
  const parsed = new URL(rawUrl);
  parsed.search = "";
  if (parsed.pathname.length > 1) {
    parsed.pathname = parsed.pathname.replace(/\/+$/u, "");
    if (parsed.pathname.length === 0) {
      parsed.pathname = "/";
    }
  }
  return parsed.toString();
}

function assertViewport(name) {
  if (!VIEWPORTS.has(name)) {
    fail(`--viewport must be desktop or mobile: ${name}`);
  }
  return VIEWPORTS.get(name);
}

function isDownloadLike(url, hasDownloadAttribute) {
  if (hasDownloadAttribute) {
    return true;
  }
  const extension = path.extname(url.pathname).toLowerCase();
  return DOWNLOAD_EXTENSIONS.has(extension);
}

function classifyLink(rawHref, baseUrl, startOrigin, hasDownloadAttribute) {
  if (rawHref.trim().length === 0) {
    return { follow: false, reason: "empty-href" };
  }

  let target;
  try {
    target = new URL(rawHref, baseUrl);
  } catch (error) {
    return { follow: false, reason: "invalid-url", message: error.message };
  }

  if (target.protocol === "mailto:" || target.protocol === "tel:") {
    return { follow: false, reason: target.protocol.slice(0, -1), url: target.toString() };
  }
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    return { follow: false, reason: "unsupported-protocol", url: target.toString() };
  }
  if (target.origin !== startOrigin) {
    return { follow: false, reason: "external-origin", url: target.toString() };
  }
  if (isDownloadLike(target, hasDownloadAttribute)) {
    return { follow: false, reason: "download", url: target.toString() };
  }

  return { follow: true, url: target.toString(), normalizedUrl: normalizeUrl(target.toString()) };
}

function compactText(value) {
  return value.replace(/\s+/gu, " ").trim();
}

function triggerLabel(prefix, text, url) {
  const cleanText = compactText(text);
  if (cleanText.length > 0) {
    return `${prefix}: ${cleanText.slice(0, 80)}`;
  }
  return `${prefix}: ${url}`;
}

function serializeError(error) {
  if (error instanceof Error) {
    return { message: error.message, stack: error.stack };
  }
  return { message: String(error) };
}

class CrawlGraph {
  constructor(maxPages) {
    this.maxPages = maxPages;
    this.screens = [];
    this.edges = [];
    this.unreachedLinks = [];
    this.screenByNormalizedUrl = new Map();
    this.edgeKeys = new Set();
  }

  ensureScreen(url, reachedFrom) {
    const normalizedUrl = normalizeUrl(url);
    const existing = this.screenByNormalizedUrl.get(normalizedUrl);
    if (existing !== undefined) {
      if (reachedFrom !== undefined) {
        existing.reachedFrom.push(reachedFrom);
      }
      return existing;
    }
    if (this.screens.length >= this.maxPages) {
      this.unreachedLinks.push({ url: normalizedUrl, reason: "max-pages" });
      return null;
    }
    const index = this.screens.length + 1;
    const screen = {
      id: `screen-${index}`,
      url: normalizedUrl,
      title: "",
      screenshot: "",
      reachedFrom: [],
      explored: false,
    };
    if (reachedFrom !== undefined) {
      screen.reachedFrom.push(reachedFrom);
    }
    this.screens.push(screen);
    this.screenByNormalizedUrl.set(normalizedUrl, screen);
    return screen;
  }

  addEdge(from, to, trigger) {
    const edgeKey = `${from.id}\u0000${to.id}\u0000${trigger}`;
    if (this.edgeKeys.has(edgeKey)) {
      return;
    }
    this.edgeKeys.add(edgeKey);
    this.edges.push({ from: from.id, to: to.id, trigger });
  }
}

async function gotoPage(page, url) {
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: LOAD_TIMEOUT_MS });
  await page.waitForTimeout(SETTLE_MS);
  return response;
}

async function evaluateInteractables(page) {
  return page.evaluate(() => {
    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
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

    const links = Array.from(document.querySelectorAll("a[href]")).map((element) => ({
      href: element.getAttribute("href"),
      resolvedHref: element.href,
      text: element.textContent ?? "",
      download: element.hasAttribute("download"),
    }));

    const buttonElements = Array.from(document.querySelectorAll('button,[role="button"],[onclick]')).filter((element) => {
      if (!isVisible(element)) {
        return false;
      }
      if (element instanceof HTMLAnchorElement) {
        return element.hasAttribute("onclick") || element.getAttribute("role") === "button";
      }
      return true;
    });
    const buttons = buttonElements.map((element, index) => ({
      index,
      selector: cssPath(element),
      text: element.textContent ?? element.getAttribute("aria-label") ?? "",
      tagName: element.tagName.toLowerCase(),
    }));

    return { links, buttons };
  });
}

async function saveScreen(page, outDir, screen) {
  const screenshot = path.join("screenshots", `${screen.id}.png`);
  await page.screenshot({ path: path.join(outDir, screenshot), fullPage: true });
  screen.screenshot = screenshot;
  screen.title = await page.title();
}

async function restoreScreen(page, screen) {
  await gotoPage(page, screen.url);
}

async function clickButtons(page, graph, screen, buttons, startOrigin) {
  for (const button of buttons) {
    await restoreScreen(page, screen);
    const beforeUrl = page.url();
    const beforeNormalizedUrl = normalizeUrl(beforeUrl);
    const trigger = triggerLabel("button", button.text, button.selector);

    await page.locator(button.selector).click({ timeout: 3000, noWaitAfter: true });
    await page.waitForLoadState("domcontentloaded", { timeout: LOAD_TIMEOUT_MS });
    await page.waitForTimeout(SETTLE_MS);

    const afterUrl = page.url();
    const afterParsed = parseHttpUrl(afterUrl, "Navigated URL");
    if (afterParsed.origin !== startOrigin) {
      graph.unreachedLinks.push({
        url: afterUrl,
        reason: "external-origin-after-click",
        trigger,
        from: screen.id,
      });
      await restoreScreen(page, screen);
      continue;
    }

    const afterNormalizedUrl = normalizeUrl(afterUrl);
    if (afterNormalizedUrl !== beforeNormalizedUrl) {
      const target = graph.ensureScreen(afterNormalizedUrl, { screenId: screen.id, trigger });
      if (target !== null) {
        graph.addEdge(screen, target, trigger);
      }
    }
  }
}

async function crawl() {
  const args = parseArgs(process.argv.slice(2));
  const startUrl = parseHttpUrl(requiredArg(args, "url"), "--url");
  const outDir = requiredArg(args, "out");
  const maxPages = parsePositiveInteger(optionalArg(args, "max-pages", "30"), "max-pages");
  const viewportName = optionalArg(args, "viewport", "desktop");
  const viewport = assertViewport(viewportName);
  const storageState = args.has("storage-state") ? args.get("storage-state") : undefined;

  await fs.mkdir(path.join(outDir, "screenshots"), { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const contextOptions = { viewport };
  if (storageState !== undefined) {
    contextOptions.storageState = storageState;
  }

  const graph = new CrawlGraph(maxPages);
  const consoleErrors = [];
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  page.setDefaultTimeout(LOAD_TIMEOUT_MS);
  page.setDefaultNavigationTimeout(LOAD_TIMEOUT_MS);

  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push({
        type: "console.error",
        message: message.text(),
        url: page.url(),
        timestamp: new Date().toISOString(),
      });
    }
  });
  page.on("pageerror", (error) => {
    consoleErrors.push({
      type: "pageerror",
      message: error.message,
      stack: error.stack,
      url: page.url(),
      timestamp: new Date().toISOString(),
    });
  });

  graph.ensureScreen(startUrl.toString());

  for (let cursor = 0; cursor < graph.screens.length; cursor += 1) {
    const screen = graph.screens[cursor];
    if (screen.explored) {
      continue;
    }

    try {
      const response = await gotoPage(page, screen.url);
      if (cursor === 0 && response !== null && response.status() >= 400) {
        throw new Error(`Start URL returned HTTP ${response.status()}: ${screen.url}`);
      }
    } catch (error) {
      if (cursor === 0) {
        throw new Error(`Start URL is unreachable: ${screen.url}. ${serializeError(error).message}`);
      }
      graph.unreachedLinks.push({
        url: screen.url,
        reason: "navigation-failed",
        message: serializeError(error).message,
      });
      continue;
    }

    await saveScreen(page, outDir, screen);
    screen.explored = true;

    const interactables = await evaluateInteractables(page);
    for (const link of interactables.links) {
      const href = link.resolvedHref ?? link.href;
      const classification = classifyLink(href, page.url(), startUrl.origin, link.download);
      const trigger = triggerLabel("link", link.text, href);
      if (!classification.follow) {
        graph.unreachedLinks.push({
          url: classification.url ?? href,
          reason: classification.reason,
          message: classification.message,
          from: screen.id,
          trigger,
        });
        continue;
      }
      const target = graph.ensureScreen(classification.normalizedUrl, { screenId: screen.id, trigger });
      if (target !== null) {
        graph.addEdge(screen, target, trigger);
      }
    }

    await clickButtons(page, graph, screen, interactables.buttons, startUrl.origin);
  }

  const transitions = {
    screens: graph.screens.map(({ explored, ...screen }) => screen),
    edges: graph.edges,
    unreachedLinks: graph.unreachedLinks,
    consoleErrors,
  };
  await fs.writeFile(path.join(outDir, "transitions.json"), `${JSON.stringify(transitions, null, 2)}\n`);
  await browser.close();
}

crawl().catch((error) => {
  fail(serializeError(error).message);
});

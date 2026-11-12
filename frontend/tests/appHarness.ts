import { spawn } from "node:child_process"
import type { ChildProcess } from "node:child_process"
import { createServer } from "node:net"
import type { AddressInfo } from "node:net"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { chromium } from "playwright"
import type { Browser, Page, Route } from "playwright"
import en from "../src/locales/en.json" with { type: "json" }

// Drives the real app in Chromium through the `playwright` library, from inside the existing
// `node --test` entry point: every /api/* request is answered by the test and every request that
// leaves the harness origin is aborted, so no backend or external host is reached and
// `playwright test` is never invoked.

export type ApiStub = (route: Route) => void | Promise<void>

export type LaunchOptions = {
  // Seeded before the app's scripts run, because i18next detects the language from localStorage.
  localStorage?: Record<string, string>
}

export type AppUnderTest = {
  page: Page
  // "<METHOD> <path>" per intercepted /api/* request, in order.
  calls: string[]
  close: () => Promise<void>
}

type FulfilledJson = { status: number; contentType: string; body: string }

type Vite = { child: ChildProcess; output: () => string }

const frontendDir = fileURLToPath(new URL("..", import.meta.url))

// Asserted copy comes from the locale file the app bundles, so a test never re-types UI strings.
export function copy(path: string): string {
  const value = path.split(".").reduce<unknown>(
    (node, part) => (typeof node === "object" && node !== null
      ? (node as Record<string, unknown>)[part]
      : undefined),
    en,
  )
  if (typeof value !== "string") throw new Error(`en.json has no string at ${path}`)
  return value
}

export function jsonRoute(body: unknown, status = 200): FulfilledJson {
  return { status, contentType: "application/json", body: JSON.stringify(body) }
}

export async function launchApp(
  stubs: Record<string, ApiStub>,
  options: LaunchOptions = {},
): Promise<AppUnderTest> {
  const port = await freePort()
  const baseUrl = `http://127.0.0.1:${port}`
  const vite = startVite(port)
  const browser = await launchBrowser(vite, baseUrl)

  const calls: string[] = []
  const unstubbed: string[] = []
  const external: string[] = []
  let page: Page | undefined
  try {
    page = await browser.newPage()
    if (options.localStorage) await pinLocalStorage(page, options.localStorage)
    // Registered first because Playwright runs the last matching route first, so /api keeps its own.
    await isolateToBaseUrl(page, baseUrl, external)
    await stubApi(page, stubs, calls, unstubbed)
    await page.goto(baseUrl)
    return { page, calls, close: () => closeApp(page, browser, vite, baseUrl, unstubbed, external) }
  } catch (error) {
    await shutdown(page, browser, vite)
    throw error
  }
}

async function launchBrowser(vite: Vite, baseUrl: string): Promise<Browser> {
  try {
    await waitForServer(baseUrl, vite)
  } catch (error) {
    await stopVite(vite.child)
    throw error
  }
  try {
    return await chromium.launch()
  } catch (error) {
    await stopVite(vite.child)
    const reason = error instanceof Error ? error.message : String(error)
    throw new Error(
      `Could not launch Chromium: ${reason}\nInstall the browser once with: npx playwright install chromium`,
    )
  }
}

async function pinLocalStorage(page: Page, values: Record<string, string>): Promise<void> {
  await page.addInitScript((seeded: Record<string, string>) => {
    for (const [key, value] of Object.entries(seeded)) localStorage.setItem(key, value)
  }, values)
}

// A request for another origin (font, CDN, telemetry added later) is aborted and named by close(),
// the same loud failure an unstubbed /api/* path gets, instead of silently reaching the network.
async function isolateToBaseUrl(page: Page, baseUrl: string, external: string[]): Promise<void> {
  await page.route("**/*", async (route) => {
    const url = route.request().url()
    if (new URL(url).origin === baseUrl) {
      await route.fallback()
      return
    }
    external.push(url)
    await route.abort("blockedbyclient")
  })
}

async function stubApi(
  page: Page,
  stubs: Record<string, ApiStub>,
  calls: string[],
  unstubbed: string[],
): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const label = `${route.request().method()} ${pathname}`
    calls.push(label)
    const prefix = Object.keys(stubs)
      .filter((key) => pathname.startsWith(key))
      .sort((left, right) => right.length - left.length)[0]
    const stub = prefix === undefined ? undefined : stubs[prefix]
    if (!stub) {
      // An unexpected request fails the test instead of reaching a server or answering a fake 200.
      unstubbed.push(label)
      await route.fulfill({ status: 404, contentType: "text/plain", body: `no stub for ${label}` })
      return
    }
    await stub(route)
  })
}

async function shutdown(page: Page | undefined, browser: Browser, vite: Vite): Promise<void> {
  await page?.close().catch(() => {})
  await browser.close().catch(() => {})
  await stopVite(vite.child)
}

async function closeApp(
  page: Page,
  browser: Browser,
  vite: Vite,
  baseUrl: string,
  unstubbed: string[],
  external: string[],
): Promise<void> {
  await shutdown(page, browser, vite)
  const problems = [
    unstubbed.length > 0 ? `no stub for: ${unstubbed.join(", ")}` : "",
    external.length > 0 ? `request left ${baseUrl}: ${external.join(", ")}` : "",
  ].filter((problem) => problem !== "")
  if (problems.length > 0) throw new Error(problems.join("\n"))
}

async function freePort(): Promise<number> {
  const server = createServer()
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", resolve)
  })
  const port = (server.address() as AddressInfo).port
  await new Promise<void>((resolve) => server.close(() => resolve()))
  return port
}

// `--host 127.0.0.1` is required: vite's default binding does not answer 127.0.0.1, and
// `--strictPort` turns a port that got taken between freePort() and now into a visible failure.
function startVite(port: number): Vite {
  const child = spawn(
    join(frontendDir, "node_modules", ".bin", "vite"),
    ["--port", String(port), "--strictPort", "--host", "127.0.0.1"],
    { cwd: frontendDir, stdio: ["ignore", "pipe", "pipe"] },
  )
  let log = ""
  child.stdout?.on("data", (chunk: Buffer) => { log += chunk.toString() })
  child.stderr?.on("data", (chunk: Buffer) => { log += chunk.toString() })
  return { child, output: () => log }
}

async function waitForServer(baseUrl: string, vite: Vite): Promise<void> {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    if (vite.child.exitCode !== null) {
      throw new Error(`vite exited with code ${vite.child.exitCode} before serving ${baseUrl}\n${vite.output()}`)
    }
    try {
      if ((await fetch(baseUrl)).ok) return
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  throw new Error(`vite did not serve ${baseUrl} within 20s\n${vite.output()}`)
}

// The child could ignore kill(); after 5 s SIGKILL keeps the suite moving instead of stalling.
function stopVite(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve()
  return new Promise((resolve) => {
    const deadline = setTimeout(() => {
      child.kill("SIGKILL")
      resolve()
    }, 5_000)
    child.once("exit", () => {
      clearTimeout(deadline)
      resolve()
    })
    child.kill()
  })
}

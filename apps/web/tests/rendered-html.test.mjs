import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the candidate profile product shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Campus Agent · 候选人画像工作台<\/title>/i);
  assert.match(html, /把一份简历/);
  assert.match(html, /可追溯的能力画像/);
  assert.match(html, /本地模式/);
  assert.match(html, /og-candidate-profile\.png/);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton|codex-preview/);
});

test("starter preview is removed and local API remains configurable", async () => {
  const [page, workspace, layout, css] = await Promise.all([
    readFile(new URL("app/page.tsx", root), "utf8"),
    readFile(new URL("app/candidate-workspace.tsx", root), "utf8"),
    readFile(new URL("app/layout.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);
  assert.match(page, /CandidateWorkspace/);
  assert.match(workspace, /NEXT_PUBLIC_CAMPUS_API_URL/);
  assert.match(workspace, /application\/pdf/);
  assert.match(workspace, /candidate\/interaction/);
  assert.match(layout, /lang="zh-CN"/);
  assert.match(css, /--orange:\s*#ff5b22/);
  await assert.rejects(access(new URL("app/_sites-preview/SkeletonPreview.tsx", root)));
});

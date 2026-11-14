"use strict";
// Offline mocks only. No browser or real network requests are made.
const assert = require("node:assert/strict");
const {createLmsCaptureClient} = require("../skills/lms-course-archive/scripts/browser_capture.js");
const origin = "https://lms.example.invalid";
const token = "synthetic-token-never-real-secret-12345";

function response(data, url, status = 200, extra = {}) {
  return {ok: status >= 200 && status < 300, status, url, type: "basic",
    json: async () => data, blob: async () => new Blob(["hello"]), headers: new Headers(), ...extra};
}
function client(fetch, extra = {}) {
  return createLmsCaptureClient({authorizedPageFetch: true, receiver: "http://127.0.0.1:43210", token,
    observedUrls: ["/list", "/file"], ...extra}, {location: {origin}, fetch});
}

(async () => {
  let calls = [];
  let cap = client(async (url, init) => {
    calls.push([url, init]);
    return url.endsWith("page=2") ? response({results: [3], paging: {}}, url) : response({results: [1, 2], paging: {nextPage: "/list?page=2"}}, url);
  });
  assert.equal(calls.length, 0, "factory must not fetch automatically");
  let result = await cap.all("/list", {expectedTotal: 3});
  assert.equal(result.complete, true);
  assert.deepEqual(result.results, [1, 2, 3]);
  assert.equal(result.pages, 2);
  assert.equal(calls[0][1].method, "GET");
  assert.equal(calls[0][1].redirect, "error");
  await assert.rejects(cap.getJSON("/guessed"), /not registered/);
  await assert.rejects(cap.getJSON("https://outside.invalid/list"), /Cross-origin/);
  assert.throws(() => client(async () => {}, {observedUrls: ["https://outside.invalid/api"]}), /Cross-origin/);
  assert.throws(() => client(async () => {}, {receiver: "http://localhost:43210"}), /127.0.0.1/);
  assert.throws(() => client(async () => {}, {authorizedPageFetch: false}), /acknowledgement/);

  cap = client(async url => response({results: [1], paging: {nextPage: "/list"}}, url));
  assert.equal((await cap.all("/list")).reason, "pagination_loop");
  cap = client(async url => response({results: [1], paging: {nextPage: "/list?next=2"}}, url));
  assert.equal((await cap.all("/list", {maxPages: 1})).reason, "page_limit");
  cap = client(async url => response({results: [1], paging: {nextPage: "https://outside.invalid/api"}}, url));
  assert.equal((await cap.all("/list")).reason, "next_page_origin_rejected");
  cap = client(async url => response({results: [1], paging: {}}, url));
  assert.equal((await cap.all("/list", {expectedTotal: 2})).reason, "expected_total_mismatch");
  cap = client(async url => response({results: [1]}, url));
  assert.equal((await cap.all("/list")).reason, "request_or_schema_error", "unknown pagination must not silently count as exhausted");
  assert.equal((await cap.all("/list", {next: () => null})).complete, true, "verified single-page adapters may declare the end explicitly");
  cap = client(async url => response({unexpected: [1]}, url));
  assert.equal((await cap.all("/list")).reason, "request_or_schema_error");
  cap = client(async url => response({}, url, 403));
  assert.equal((await cap.all("/list")).complete, false);

  calls = [];
  cap = client(async (url, init) => {
    calls.push([url, init]);
    if (url.startsWith("http://127.0.0.1")) return response({ok: true, status: "saved"}, url);
    return response(null, "https://files.example.invalid/asset?signature=private", 200);
  }, {fileCredentials: "omit"});
  result = await cap.file("/file");
  assert.equal(result.bytes, 5);
  assert.equal(result.finalSource, "https://files.example.invalid/asset");
  assert.equal(calls[0][1].credentials, "omit");
  assert.equal(calls[0][1].redirect, "follow");
  assert.equal(calls.length, 1, "download must not automatically save");
  await cap.save("unit/file.bin", result.blob);
  assert.equal(calls[1][1].headers["X-Capture-Token"], token);
  assert.equal(calls[1][1].credentials, "omit");
  await cap.close();
  await assert.rejects(cap.save("later", "x"), /closed/);

  cap = client(async url => response(null, url, 206, {headers: new Headers({"Content-Range": "bytes 0-4/10"})}));
  await assert.rejects(cap.file("/file"), /Partial file/);
  cap = client(async url => response(null, url, 206, {headers: new Headers({"Content-Range": "bytes 0-4/5"})}));
  assert.equal((await cap.file("/file")).complete, true);
  console.log("browser_capture: offline pagination, policy, file and receiver mocks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });

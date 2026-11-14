/* Optional helper. Load only when the host permits page JavaScript + fetch.
 * Defining this factory performs no requests. No browser/tool-policy bypass.
 * Caller supplies URLs already observed in the current authorized LMS session.
 */
(function (root) {
  "use strict";

  function createLmsCaptureClient(config, environment = globalThis) {
    if (!config || config.authorizedPageFetch !== true) {
      throw new Error("Explicit host-authorized page fetch acknowledgement is required");
    }
    const origin = new URL(environment.location.origin).origin;
    const fetchImpl = environment.fetch.bind(environment);
    let token = config.token;
    if (typeof token !== "string" || token.length < 32) throw new Error("Receiver token is required");
    const receiver = new URL(config.receiver);
    if (receiver.protocol !== "http:" || receiver.hostname !== "127.0.0.1" || !receiver.port ||
        receiver.username || receiver.password || receiver.pathname !== "/" || receiver.search || receiver.hash) {
      throw new Error("Receiver must be an explicit http://127.0.0.1:PORT origin");
    }
    const receiverOrigin = receiver.origin;
    const observed = new Set();
    const credentials = config.credentials || "same-origin";
    const fileCredentials = config.fileCredentials || credentials;
    if (![credentials, fileCredentials].every(v => ["same-origin", "include", "omit"].includes(v))) {
      throw new Error("Unsupported credentials mode");
    }

    function absolute(value) {
      // Resolve against the LMS origin, never an HTML <base> that may point at a CDN.
      const url = new URL(value, origin + "/");
      if (url.origin !== origin || url.username || url.password || !["https:", "http:"].includes(url.protocol)) {
        throw new Error("Cross-origin or credential-bearing API URL rejected");
      }
      url.hash = "";
      return url.href;
    }
    function observe(value) {
      const url = absolute(value);
      observed.add(url);
      return url;
    }
    for (const url of config.observedUrls || []) observe(url);
    function checked(value) {
      const url = absolute(value);
      if (!observed.has(url)) throw new Error("URL was not registered as observed in this LMS session");
      return url;
    }
    function publicUrl(value) {
      const url = new URL(value);
      url.search = "";
      url.hash = "";
      return url.href;
    }
    async function getJSON(value) {
      const url = checked(value);
      const response = await fetchImpl(url, {method: "GET", credentials, redirect: "error"});
      if (!response.ok || response.status === 206) throw new Error("JSON request failed or was partial (status " + response.status + ")");
      if (response.url && new URL(response.url).origin !== origin) throw new Error("Cross-origin JSON response rejected");
      let data;
      try { data = await response.json(); } catch (_) { throw new Error("JSON response was not parseable; inspect login and endpoint schema"); }
      return {ok: true, status: response.status, source: publicUrl(url), data};
    }
    async function all(value, options = {}) {
      const maxPages = options.maxPages === undefined ? 100 : options.maxPages;
      if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 1000) throw new Error("maxPages must be 1..1000");
      const itemsOf = options.items || (data => data.results);
      const nextOf = options.next || (data => {
        if (!data.paging || typeof data.paging !== "object" || Array.isArray(data.paging)) {
          throw new Error("unrecognized_pagination_schema");
        }
        return data.paging.nextPage;
      });
      let next = checked(value), results = [], pages = 0;
      const visited = new Set(), sources = [];
      while (next) {
        if (pages >= maxPages) return {complete: false, reason: "page_limit", results, pages, sources};
        if (visited.has(next)) return {complete: false, reason: "pagination_loop", results, pages, sources};
        visited.add(next);
        let page, items, following;
        try {
          page = await getJSON(next);
          items = itemsOf(page.data);
          following = nextOf(page.data);
          if (!Array.isArray(items)) throw new Error("unrecognized_items_schema");
          if (following !== undefined && following !== null && following !== "" && typeof following !== "string") {
            throw new Error("unrecognized_next_page_schema");
          }
        } catch (_) { return {complete: false, reason: "request_or_schema_error", results, pages, sources}; }
        results = results.concat(items);
        pages += 1;
        sources.push(page.source);
        try {
          // The next URL has now been observed in the live response, but still
          // must be same-origin. This does not authorize guessed endpoint scans.
          next = following ? observe(following) : null;
        } catch (_) { return {complete: false, reason: "next_page_origin_rejected", results, pages, sources}; }
      }
      if (options.expectedTotal !== undefined && results.length !== options.expectedTotal) {
        return {complete: false, reason: "expected_total_mismatch", results, pages, sources};
      }
      return {complete: true, reason: "declared_pagination_exhausted", results, pages, sources};
    }
    async function file(value) {
      const url = checked(value);
      // Ordinary authenticated file links may redirect to a file service.
      // Credentials mode is explicit/configurable; no value works for every LMS.
      const response = await fetchImpl(url, {method: "GET", credentials: fileCredentials, redirect: "follow"});
      if (!response.ok || response.type === "opaque") throw new Error("File download failed or is unreadable (status " + response.status + ")");
      const blob = await response.blob();
      const range = response.headers.get("content-range");
      if (response.status === 206 && !range) throw new Error("Partial response without a complete content-range");
      if (range) {
        const match = /^bytes\s+(\d+)-(\d+)\/(\d+)$/i.exec(range);
        if (!match || Number(match[1]) !== 0 || Number(match[2]) !== blob.size - 1 || Number(match[3]) !== blob.size) {
          throw new Error("Partial file response rejected");
        }
      }
      return {ok: true, complete: true, status: response.status, bytes: blob.size,
              mime: response.headers.get("content-type"), source: publicUrl(url),
              finalSource: publicUrl(response.url || url), blob};
    }
    async function receiverRequest(path, init = {}) {
      if (!token) throw new Error("Receiver session was closed");
      const response = await fetchImpl(receiverOrigin + path, {...init, credentials: "omit", redirect: "error",
        headers: {...init.headers, "X-Capture-Token": token}});
      let result;
      try { result = await response.json(); } catch (_) { throw new Error("Receiver response was not JSON"); }
      if (!response.ok || !result.ok) throw new Error("Receiver rejected request: " + (result.error || response.status));
      return result;
    }
    function save(relativePath, body) {
      if (typeof relativePath !== "string" || !relativePath) throw new Error("An explicit relative output path is required");
      return receiverRequest("/save", {method: "POST", headers: {"X-Capture-Path": encodeURIComponent(relativePath)}, body});
    }
    function saveJSON(relativePath, value) { return save(relativePath, JSON.stringify(value, null, 2) + "\n"); }
    function exists(relativePath) { return receiverRequest("/exists?path=" + encodeURIComponent(relativePath), {method: "GET"}); }
    async function close() {
      const result = await receiverRequest("/shutdown", {method: "POST", body: ""});
      token = "";
      return result;
    }
    return Object.freeze({origin, observe, getJSON, all, file, save, saveJSON, exists, close});
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {createLmsCaptureClient};
  else root.createLmsCaptureClient = createLmsCaptureClient;
})(globalThis);

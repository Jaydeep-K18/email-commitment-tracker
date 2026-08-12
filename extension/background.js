// Service worker — the only place that talks to the tracker.
//
// Manifest V3 removed cross-origin privileges from content scripts: a fetch
// issued from content.js carries mail.google.com as its origin and is subject
// to that page's CORS, so it would be blocked. The service worker still has the
// extension's host_permissions, and its requests carry the chrome-extension://
// origin the tracker's CORS rule allows. Everything therefore goes through here
// and content.js talks to it by message passing.

const DEFAULT_BASE = "http://127.0.0.1:8765";
const TOKEN_HEADER = "X-Tracker-Token";

async function settings() {
  const stored = await chrome.storage.local.get(["token", "baseUrl"]);
  return {
    token: stored.token || "",
    baseUrl: (stored.baseUrl || DEFAULT_BASE).replace(/\/+$/, ""),
  };
}

async function call(path, { method = "GET", body = null } = {}) {
  const { token, baseUrl } = await settings();
  if (!token) {
    return {
      ok: false,
      status: 0,
      error:
        "No token set. Open the extension's options and paste the token from " +
        "the tracker's setup page.",
    };
  }

  let response;
  try {
    response = await fetch(baseUrl + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        [TOKEN_HEADER]: token,
      },
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    // Distinguish "app is not running" from every other failure, because that
    // is overwhelmingly the reason and the fix is obvious once stated.
    return {
      ok: false,
      status: 0,
      error:
        "Could not reach the tracker. Is the desktop app running? " +
        `(${err.message})`,
    };
  }

  if (response.status === 401) {
    return {
      ok: false,
      status: 401,
      error: "The tracker rejected this token. Check the extension options.",
    };
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail = data && data.detail ? data.detail : response.statusText;
    return { ok: false, status: response.status, error: String(detail) };
  }
  return { ok: true, status: response.status, data };
}

const ROUTES = {
  status: () => call("/api/status"),
  analyze: (payload) => call("/api/analyze", { method: "POST", body: payload }),
  lookup: (payload) =>
    call(
      "/api/lookup?subject=" +
        encodeURIComponent(payload.subject || "") +
        "&thread_id=" +
        encodeURIComponent(payload.thread_id || "")
    ),
  add: (payload) => call("/api/commitments", { method: "POST", body: payload }),
  sync: () => call("/api/sync", { method: "POST" }),
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = ROUTES[message && message.action];
  if (!handler) {
    sendResponse({ ok: false, error: `Unknown action: ${message?.action}` });
    return false;
  }
  // Returning true keeps the message channel open for the async reply.
  handler(message.payload || {}).then(sendResponse);
  return true;
});

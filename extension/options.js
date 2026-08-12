// Where the user pastes the token. Kept deliberately small: it stores two
// values and offers a live check, so a misconfiguration is caught here rather
// than showing up later as a silent panel inside Gmail.

const DEFAULT_BASE = "http://127.0.0.1:8765";

const tokenInput = document.getElementById("token");
const baseInput = document.getElementById("baseUrl");
const statusLine = document.getElementById("status");

const say = (text, kind = "muted") => {
  statusLine.textContent = text;
  statusLine.className = kind;
};

async function load() {
  const stored = await chrome.storage.local.get(["token", "baseUrl"]);
  tokenInput.value = stored.token || "";
  baseInput.value = stored.baseUrl || DEFAULT_BASE;
}

async function save() {
  await chrome.storage.local.set({
    token: tokenInput.value.trim(),
    baseUrl: (baseInput.value.trim() || DEFAULT_BASE).replace(/\/+$/, ""),
  });
  say("Saved.", "ok");
}

async function test() {
  // Save first, so Test always checks what the user is looking at rather than
  // whatever was stored on the last visit.
  await save();
  say("Checking…");

  chrome.runtime.sendMessage({ action: "status" }, (reply) => {
    if (!reply || !reply.ok) {
      say((reply && reply.error) || "No response from the extension.", "error");
      return;
    }
    const data = reply.data || {};
    const calendar = data.google_connected
      ? "Google Calendar connected"
      : "local .ics only — connect Google in the dashboard";
    say(
      `Connected. ${data.open_commitments} open commitment(s); ${calendar}.`,
      "ok"
    );
  });
}

document.getElementById("save").addEventListener("click", save);
document.getElementById("test").addEventListener("click", test);
load();

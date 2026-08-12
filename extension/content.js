// The panel that appears inside Gmail when a message is open.
//
// Gmail's markup is generated and its class names are short and undocumented,
// so every selector here is a guess that Google can invalidate without notice.
// The code therefore treats "I could not read the message" as an expected
// outcome and says so, rather than sending an empty body to the model and
// reporting "no commitments found" — which looks identical to a genuinely
// uneventful email and would quietly train the user to distrust the panel.

(() => {
  "use strict";

  const PANEL_ID = "ect-panel";
  const MIN_BODY_CHARS = 20;

  /** Selectors are ordered most- to least-specific; the first hit wins. */
  const SELECTORS = {
    subject: ["h2.hP", "h2[data-thread-perm-id]", ".ha h2"],
    senderSpan: ["span.gD", ".gE span[email]"],
    body: ["div.a3s", "div[data-message-id] div.ii"],
  };

  const firstMatch = (selectors, root = document) => {
    for (const selector of selectors) {
      const found = root.querySelector(selector);
      if (found) return found;
    }
    return null;
  };

  const allMatches = (selectors, root = document) => {
    for (const selector of selectors) {
      const found = root.querySelectorAll(selector);
      if (found.length) return Array.from(found);
    }
    return [];
  };

  /** The message currently on screen, or null if none can be read. */
  function readOpenMessage() {
    const subjectEl = firstMatch(SELECTORS.subject);
    if (!subjectEl) return null;

    const senderEl = firstMatch(SELECTORS.senderSpan);
    // The last body block is the newest message in a thread, which is the one
    // the user is almost certainly reading.
    const bodies = allMatches(SELECTORS.body);
    const bodyEl = bodies.length ? bodies[bodies.length - 1] : null;

    return {
      subject: (subjectEl.innerText || "").trim(),
      sender_name: senderEl ? (senderEl.getAttribute("name") || "").trim() : "",
      sender_email: senderEl ? (senderEl.getAttribute("email") || "").trim() : "",
      body: bodyEl ? (bodyEl.innerText || "").trim() : "",
      thread_id: location.hash.split("/").pop() || null,
    };
  }

  const send = (action, payload) =>
    new Promise((resolve) => {
      chrome.runtime.sendMessage({ action, payload }, (reply) =>
        resolve(reply || { ok: false, error: "The extension did not respond." })
      );
    });

  // --- Rendering -----------------------------------------------------------

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function ensurePanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) return panel;

    panel = el("div", "ect-panel");
    panel.id = PANEL_ID;

    const header = el("div", "ect-panel__header");
    header.appendChild(el("span", "ect-panel__dot"));
    header.appendChild(el("span", "ect-panel__title", "Commitment Tracker"));

    const collapse = el("button", "ect-panel__collapse", "–");
    collapse.title = "Hide";
    collapse.addEventListener("click", () => panel.classList.toggle("ect-panel--collapsed"));
    header.appendChild(collapse);

    panel.appendChild(header);
    panel.appendChild(el("div", "ect-panel__body"));
    document.body.appendChild(panel);
    return panel;
  }

  function setBody(render) {
    const body = ensurePanel().querySelector(".ect-panel__body");
    body.textContent = "";
    render(body);
  }

  const showMessage = (text, kind = "muted") =>
    setBody((body) => body.appendChild(el("p", `ect-note ect-note--${kind}`, text)));

  function formatDeadline(iso) {
    if (!iso) return "no date";
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return "no date";
    const midnight =
      when.getHours() === 0 && when.getMinutes() === 0;
    return when.toLocaleString(undefined, {
      weekday: "short",
      day: "2-digit",
      month: "short",
      ...(midnight ? {} : { hour: "2-digit", minute: "2-digit" }),
    });
  }

  function renderFound(commitments, message) {
    setBody((body) => {
      if (message) {
        body.appendChild(el("p", "ect-note ect-note--muted", message));
      }
      commitments.forEach((commitment) => {
        const card = el("div", "ect-item");
        card.appendChild(el("div", "ect-item__subject", commitment.subject));

        const meta = el("div", "ect-item__meta");
        meta.appendChild(el("span", "ect-chip", formatDeadline(commitment.deadline)));
        meta.appendChild(
          el("span", "ect-chip ect-chip--soft", `${Math.round(commitment.confidence * 100)}%`)
        );
        card.appendChild(meta);

        if (commitment.evidence_quote) {
          card.appendChild(
            el("div", "ect-item__quote", `“${commitment.evidence_quote}”`)
          );
        }

        const button = el("button", "ect-button", "Add to calendar");
        button.addEventListener("click", async () => {
          button.disabled = true;
          button.textContent = "Adding…";
          const reply = await send("add", {
            type: commitment.type,
            subject: commitment.subject,
            deadline: commitment.deadline,
            counterparty: commitment.counterparty,
            evidence_quote: commitment.evidence_quote,
            confidence: commitment.confidence,
            message: currentMessage,
          });
          if (!reply.ok) {
            button.textContent = "Failed";
            button.classList.add("ect-button--error");
            card.appendChild(el("p", "ect-note ect-note--error", reply.error));
            return;
          }
          button.textContent = "Added ✓";
          button.classList.add("ect-button--done");
          // Publish straight away rather than leaving the user watching an
          // empty calendar until the next half-hourly cycle.
          send("sync");
        });
        card.appendChild(button);
        body.appendChild(card);
      });
    });
  }

  function renderKnown(known) {
    setBody((body) => {
      const card = el("div", "ect-item ect-item--known");
      card.appendChild(el("div", "ect-item__subject", known.subject));
      const meta = el("div", "ect-item__meta");
      meta.appendChild(el("span", "ect-chip", formatDeadline(known.deadline)));
      meta.appendChild(
        el(
          "span",
          "ect-chip ect-chip--soft",
          known.on_calendar ? "on your calendar" : known.status
        )
      );
      card.appendChild(meta);
      card.appendChild(
        el("p", "ect-note ect-note--muted", "Already tracked — nothing to do.")
      );
      body.appendChild(card);

      const again = el("button", "ect-button ect-button--ghost", "Scan again anyway");
      again.addEventListener("click", () => analyse({ force: true }));
      body.appendChild(again);
    });
  }

  // --- Flow ----------------------------------------------------------------

  let currentMessage = null;
  let lastKey = null;
  let busy = false;

  async function analyse({ force = false } = {}) {
    if (busy) return;
    const message = readOpenMessage();

    if (!message) {
      lastKey = null;
      const panel = document.getElementById(PANEL_ID);
      if (panel) panel.remove();  // No message open: get out of the way.
      return;
    }

    const key = `${message.thread_id}|${message.subject}`;
    if (!force && key === lastKey) return;
    lastKey = key;
    currentMessage = message;

    ensurePanel();

    if (!message.body || message.body.length < MIN_BODY_CHARS) {
      setBody((body) => {
        body.appendChild(
          el(
            "p",
            "ect-note ect-note--warn",
            "Could not read this message's text. Open the email fully, then scan."
          )
        );
        const retry = el("button", "ect-button ect-button--ghost", "Scan");
        retry.addEventListener("click", () => analyse({ force: true }));
        body.appendChild(retry);
      });
      return;
    }

    busy = true;
    try {
      if (!force) {
        const known = await send("lookup", {
          subject: message.subject,
          thread_id: message.thread_id,
        });
        if (known.ok && known.data && known.data.known) {
          renderKnown(known.data);
          return;
        }
      }

      showMessage("Reading this email with the local model…");
      const reply = await send("analyze", message);

      if (!reply.ok) {
        showMessage(reply.error, "error");
        return;
      }
      const data = reply.data || {};
      if (!data.readable) {
        showMessage(data.message || "Could not read this message.", "warn");
        return;
      }
      if (!data.commitments || !data.commitments.length) {
        showMessage(data.message || "No deadlines in this email.");
        return;
      }
      renderFound(data.commitments, data.message);
    } finally {
      busy = false;
    }
  }

  // Gmail is a single-page app: the URL hash changes and the DOM is swapped
  // without a navigation, so both have to be watched. The observer is debounced
  // because Gmail mutates the DOM near-continuously.
  let timer = null;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => analyse(), 600);
  };

  window.addEventListener("hashchange", schedule);
  new MutationObserver(schedule).observe(document.body, {
    childList: true,
    subtree: true,
  });
  schedule();
})();

(() => {
  const SLOT_ID = "atfood-ai-slot";
  const API_URL =
    (typeof window !== "undefined" && window.ATFOOD_API_URL) || "/api/atfood";
  let renderFn = null;

  function ensureChat(slot) {
    let chat = slot.querySelector(".atfood-ai-chat");
    if (!chat) {
      slot.innerHTML = "";
      chat = document.createElement("div");
      chat.className = "atfood-ai-chat";
      const messages = document.createElement("div");
      messages.className = "atfood-ai-messages";
      chat.appendChild(messages);
      slot.appendChild(chat);
    }
    return {
      chat,
      messages: chat.querySelector(".atfood-ai-messages"),
    };
  }

  function appendMessage(messages, role, htmlOrText, isHtml) {
    const msg = document.createElement("div");
    msg.className = `atfood-ai-msg ${role}`;
    if (isHtml) {
      msg.innerHTML = htmlOrText;
    } else {
      msg.textContent = htmlOrText;
    }
    messages.appendChild(msg);
    return msg;
  }

  function scrollSlotToBottom(slot) {
    if (!slot) {
      return;
    }
    slot.scrollTop = slot.scrollHeight;
  }

  function defaultRender(text, options = {}) {
    const slot = document.getElementById(SLOT_ID);
    if (!slot) {
      return;
    }

    const { messages } = ensureChat(slot);
    if (options.pendingEl) {
      options.pendingEl.innerHTML = renderMarkdown(`AtFood: ${text || ""}`);
      scrollSlotToBottom(slot);
      return;
    }

    appendMessage(messages, "assistant", renderMarkdown(text), true);
    scrollSlotToBottom(slot);
  }

  function renderMarkdown(text) {
    if (typeof window !== "undefined" && window.marked?.parse) {
      const html = window.marked.parse(text || "");
      return sanitizeHtml(html);
    }
    const safe = escapeHtml(text || "");
    return `<pre style="white-space:pre-wrap;margin:0;">${safe}</pre>`;
  }

  function sanitizeHtml(html) {
    if (typeof window !== "undefined" && window.DOMPurify?.sanitize) {
      return window.DOMPurify.sanitize(html);
    }
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script, style, iframe, object, embed, link").forEach((el) => {
      el.remove();
    });
    doc.querySelectorAll("*").forEach((el) => {
      [...el.attributes].forEach((attr) => {
        const name = attr.name.toLowerCase();
        const value = String(attr.value || "");
        if (name.startsWith("on")) {
          el.removeAttribute(attr.name);
          return;
        }
        if ((name === "href" || name === "src") && value.trim().toLowerCase().startsWith("javascript:")) {
          el.removeAttribute(attr.name);
        }
      });
    });
    return doc.body.innerHTML;
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (m) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[m])
    );
  }

  function resolveToken(trigger) {
    const tokenFromElement = trigger?.getAttribute("data-atfood-token");
    if (tokenFromElement) {
      return tokenFromElement;
    }
    if (typeof window !== "undefined" && window.ATFOOD_API_TOKEN) {
      return window.ATFOOD_API_TOKEN;
    }
    return "";
  }

  function resolveUser(trigger) {
    const userFromElement = trigger?.getAttribute("data-atfood-user");
    if (userFromElement) {
      return userFromElement;
    }
    if (typeof window !== "undefined" && window.ATFOOD_USER) {
      return window.ATFOOD_USER;
    }
    return "";
  }

  function resolveSessionId(trigger) {
    const sessionFromElement = trigger?.getAttribute("data-atfood-session-id");
    if (sessionFromElement) {
      return sessionFromElement;
    }
    if (typeof window !== "undefined" && window.ATFOOD_SESSION_ID) {
      return window.ATFOOD_SESSION_ID;
    }
    return "";
  }

  async function sendAction(payload, options = {}) {
    const slot = document.getElementById(SLOT_ID);
    let pendingEl = null;
    if (slot) {
      const { messages } = ensureChat(slot);
      if (payload?.user_text) {
        appendMessage(messages, "user", `You: ${payload.user_text}`, false);
      }
      pendingEl = appendMessage(messages, "assistant", "AtFood: Thinking...", false);
      scrollSlotToBottom(slot);

      const panel = slot.closest(".panel");
      const formRow = panel?.querySelector(".formrow");
      if (panel && formRow && panel.lastElementChild !== formRow) {
        panel.appendChild(formRow);
      }
    }

    const token = options.token || resolveToken(options.trigger);
    const user = options.user || resolveUser(options.trigger);

    const headers = { "Content-Type": "application/json" };
    if (token) {
      headers["X-ATFOOD-TOKEN"] = token;
    }
    if (user) {
      headers["X-ATFOOD-USER"] = user;
    }

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        throw new Error(`ATFOOD API error (${res.status}): ${errText}`);
      }

      const data = await res.json();
      if (renderFn) {
        renderFn(data.text || "", { pendingEl, slot, typing: true });
      } else {
        defaultRender(data.text || "", { pendingEl, slot });
      }
      return data;
    } catch (err) {
      console.error(err);
      defaultRender("Sorry - something broke. Try again in a second.", {
        pendingEl,
        slot,
      });
      return null;
    }
  }

  window.ATFOOD_AI = {
    mount(fn) {
      renderFn = fn;
    },
    sendAction,
  };

  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-atfood-action]");
    if (!el) {
      return;
    }

    event.preventDefault();

    const action = el.getAttribute("data-atfood-action");
    const recipeId = el.getAttribute("data-recipe-id") || null;
    const criticTopic = el.getAttribute("data-critic-topic") || null;
    const userBox = document.querySelector("[data-atfood-user-input]");
    const userText = userBox ? userBox.value.trim() : "";
    const sessionId = resolveSessionId(el);
    if (userBox) {
      userBox.value = "";
    }
    const slot = document.getElementById(SLOT_ID);
    if (slot) {
      slot.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    sendAction(
      {
        action,
        recipe_id: recipeId,
        critic_topic: criticTopic,
        user_text: userText || null,
        session_id: sessionId || null,
      },
      { trigger: el }
    ).then(() => {
      document
        .getElementById(SLOT_ID)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, true);

  window.dispatchEvent(new CustomEvent("atfood:ready"));
})();

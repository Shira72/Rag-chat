// התנהגות מסך הצ'אט: שליחת שאלה, הצגת תשובה ומקורות, ושמירת ההיסטוריה בזיכרון הדף.

const messagesEl = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("question");
const sendBtn = document.getElementById("send");
const emptyState = document.getElementById("empty-state");

const MAX_HISTORY = 6;   // כמה הודעות אחרונות נשלחות לשרת, להבנת שאלות המשך
let history = [];        // [{role: "user"|"assistant", content: "..."}]
let busy = false;

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(className, html) {
  if (emptyState) emptyState.remove();
  const el = document.createElement("div");
  el.className = `msg ${className}`;
  el.innerHTML = html;
  messagesEl.appendChild(el);
  scrollToBottom();
  return el;
}

// [1] בטקסט התשובה הופך לקישור לכרטיס המקור המתאים
function formatAnswer(text, sourceCount, messageId) {
  return escapeHtml(text).replace(/\[(\d+)\]/g, (match, n) => {
    const i = Number(n);
    return i >= 1 && i <= sourceCount
      ? `<sup><a href="#src-${messageId}-${i}">[${i}]</a></sup>`
      : "";
  });
}

function renderSources(sources, messageId) {
  if (!sources.length) return "";
  const cards = sources.map((s, idx) => {
    const n = idx + 1;
    const links = s.links.length
      ? `<div class="ext-links">${s.links.map(l =>
          `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener noreferrer"
              onclick="event.stopPropagation()">🔗 ${escapeHtml(l.text)}</a>`).join("")}</div>`
      : "";
    return `
      <a class="source ${s.is_current ? "" : "old"}" id="src-${messageId}-${n}"
         href="${escapeHtml(s.url)}" target="_blank" rel="noopener">
        <span class="n">[${n}]</span>
        <span class="doc">${escapeHtml(s.doc_name)}</span> · ${escapeHtml(s.section_label)}
        <div class="meta">${escapeHtml(s.edition_label)}</div>
      </a>${links}`;
  }).join("");
  return `<p class="sources-title">מקורות:</p><div class="sources">${cards}</div>`;
}

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  addMessage("user", escapeHtml(question));
  const typing = addMessage("typing", "מחפש במסמכים...");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: history.slice(-MAX_HISTORY) }),
    });
    const data = await res.json().catch(() => ({}));
    typing.remove();

    if (!res.ok) {
      addMessage("error", escapeHtml(data.detail || "אירעה שגיאה. נסו שוב."));
      return;
    }
    const messageId = Date.now();
    addMessage(
      `bot ${data.found ? "" : "not-found"}`,
      formatAnswer(data.answer, data.sources.length, messageId) + renderSources(data.sources, messageId)
    );
    history.push({ role: "user", content: question });
    history.push({ role: "assistant", content: data.answer });
  } catch (err) {
    typing.remove();
    addMessage("error", "אין חיבור לשרת. ודאו שהשרת רץ ונסו שוב.");
  } finally {
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value;
  input.value = "";
  autoResize();
  ask(q);
});

// Enter שולח, Shift+Enter יורד שורה
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}
input.addEventListener("input", autoResize);

document.querySelectorAll(".example").forEach(btn =>
  btn.addEventListener("click", () => ask(btn.textContent))
);

document.getElementById("new-chat").addEventListener("click", () => {
  history = [];
  location.reload();
});

input.focus();

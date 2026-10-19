(function () {
  "use strict";
  const chatLog = document.getElementById("chatLog");
  const emptyState = document.getElementById("emptyState");
  const chatForm = document.getElementById("chatForm");
  const messageInput = document.getElementById("messageInput");
  const sendBtn = document.getElementById("sendBtn");
  const newChatBtn = document.getElementById("newChatBtn");
  const userTpl = document.getElementById("userBubbleTpl");
  const aiTpl = document.getElementById("aiBubbleTpl");
  const loadingTpl = document.getElementById("loadingBubbleTpl");
  const errorTpl = document.getElementById("errorBubbleTpl");
  let threadId = null;
  let sending = false;

  function bottom() { chatLog.scrollTop = chatLog.scrollHeight; }
  function hideEmpty() { if (emptyState) emptyState.hidden = true; }
  function addUser(text) {
    hideEmpty(); const node = userTpl.content.cloneNode(true);
    node.querySelector(".bubble-user").textContent = text; chatLog.appendChild(node); bottom();
  }
  function addLoading() {
    hideEmpty(); const node = loadingTpl.content.cloneNode(true); chatLog.appendChild(node); bottom();
    return chatLog.querySelector(".msg-loading:last-of-type");
  }
  function addError(text) {
    hideEmpty(); const node = errorTpl.content.cloneNode(true);
    node.querySelector(".bubble-error").textContent = text; chatLog.appendChild(node); bottom();
  }
  function markdown(text) {
    if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(text || ""));
    const safe = (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<p>${safe.replace(/\n/g, "<br>")}</p>`;
  }
  function setupTabs(bubble) {
    const buttons = bubble.querySelectorAll(".tab-btn");
    buttons.forEach((button) => button.addEventListener("click", () => {
      buttons.forEach((item) => item.classList.remove("active")); button.classList.add("active");
      bubble.querySelectorAll(".details-panel").forEach((panel) => { panel.hidden = panel.dataset.panel !== button.dataset.tab; });
    }));
  }
  async function resumeApproval(approved, feedback, approvalBox) {
    if (sending || !threadId) return;
    sending = true; sendBtn.disabled = true;
    try {
      const response = await fetch("/api/travel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, approved, feedback })
      });
      const data = await response.json();
      if (!response.ok || !data.success) { addError(data.error || "Không thể tiếp tục duyệt."); return; }
      approvalBox.hidden = true; addAi(data);
    } catch (error) { addError("Không thể kết nối tới máy chủ."); console.error(error); }
    finally { sending = false; sendBtn.disabled = false; }
  }
  function addAi(data) {
    hideEmpty(); const node = aiTpl.content.cloneNode(true); const bubble = node.querySelector(".bubble-ai");
    bubble.querySelector(".answer-content").innerHTML = markdown(data.answer);
    const values = { flight: data.flight_results, hotel: data.hotel_results, weather: data.weather_result, budget: data.budget_results, itinerary: data.itinerary };
    Object.entries(values).forEach(([key, value]) => {
      bubble.querySelector(`[data-panel="${key}"]`).textContent = value || "Không có dữ liệu.";
    });
    if (typeof data.llm_calls === "number") bubble.querySelector(".llm-calls").textContent = `${data.llm_calls} lần gọi AI`;
    setupTabs(bubble);
    if (data.status === "awaiting_approval") {
      const box = bubble.querySelector(".approval-box"); box.hidden = false;
      box.querySelector(".approval-request").textContent = data.approval_request || "Vui lòng duyệt bản nháp.";
      box.querySelector(".approve-btn").addEventListener("click", () => resumeApproval(true, "", box));
      box.querySelector(".revise-btn").addEventListener("click", () => {
        const feedback = window.prompt("Bạn muốn chỉnh sửa điều gì?", "");
        if (feedback !== null) resumeApproval(false, feedback, box);
      });
    }
    chatLog.appendChild(node); bottom();
  }
  async function send(text) {
    if (sending) return; sending = true; sendBtn.disabled = true; addUser(text);
    const loading = addLoading();
    try {
      const response = await fetch("/api/travel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, thread_id: threadId })
      });
      const data = await response.json(); loading.remove();
      if (!response.ok || !data.success) { addError(data.error || "Đã có lỗi xảy ra."); return; }
      threadId = data.thread_id || threadId; addAi(data);
    } catch (error) { loading.remove(); addError("Không thể kết nối tới máy chủ."); console.error(error); }
    finally { sending = false; sendBtn.disabled = false; }
  }
  chatForm.addEventListener("submit", (event) => { event.preventDefault(); const text = messageInput.value.trim(); if (!text) return; messageInput.value = ""; send(text); });
  messageInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); chatForm.requestSubmit(); } });
  messageInput.addEventListener("input", () => { messageInput.style.height = "auto"; messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px"; });
  document.querySelectorAll(".example-chip").forEach((chip) => chip.addEventListener("click", () => { messageInput.value = chip.textContent; messageInput.focus(); }));
  newChatBtn.addEventListener("click", () => { threadId = null; chatLog.innerHTML = ""; chatLog.appendChild(emptyState); emptyState.hidden = false; messageInput.focus(); });
})();

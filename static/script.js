(function () {
  "use strict";
  const chatLog = document.getElementById("chatLog");
  const emptyState = document.getElementById("emptyState");
  const chatForm = document.getElementById("chatForm");
  const messageInput = document.getElementById("messageInput");
  const sendBtn = document.getElementById("sendBtn");
  const newChatBtn = document.getElementById("newChatBtn");
  const threadLabel = document.getElementById("threadLabel");
  const systemStatus = document.getElementById("systemStatus");
  const systemStatusHint = document.getElementById("systemStatusHint");
  const userTpl = document.getElementById("userBubbleTpl");
  const aiTpl = document.getElementById("aiBubbleTpl");
  const loadingTpl = document.getElementById("loadingBubbleTpl");
  const errorTpl = document.getElementById("errorBubbleTpl");
  let threadId = null;
  let sending = false;
  let lastMessage = "";
  const statusCopy = { ready: ["Sẵn sàng lập kế hoạch", "Các agent đang chờ yêu cầu của bạn"], working: ["Đang xử lý yêu cầu", "Guardrail và supervisor đang điều phối"], approval: ["Chờ bạn phê duyệt", "Bản nháp đã sẵn sàng để review"], complete: ["Lịch trình đã hoàn tất", "Bạn có thể bắt đầu chuyến đi"], error: ["Cần thử lại", "Kiểm tra kết nối hoặc cấu hình API"] };
  function bottom() { chatLog.scrollTop = chatLog.scrollHeight; }
  function setSystemStatus(state) { const copy = statusCopy[state] || statusCopy.ready; systemStatus.textContent = copy[0]; systemStatusHint.textContent = copy[1]; document.body.dataset.appState = state; }
  function hideEmpty() { if (emptyState) emptyState.hidden = true; }
  function resetPipeline() { document.querySelectorAll("[data-pipeline]").forEach((item) => { item.className = ""; }); }
  function setPipeline(state) { const order = state === "working" ? ["guardrail", "supervisor", "data", "budget", "itinerary"] : state === "approval" ? ["guardrail", "supervisor", "data", "itinerary"] : state === "complete" ? ["guardrail", "supervisor", "data", "budget", "itinerary"] : []; document.querySelectorAll("[data-pipeline]").forEach((item) => { item.className = order.includes(item.dataset.pipeline) ? "done" : ""; }); }
  function addUser(text) { hideEmpty(); const node = userTpl.content.cloneNode(true); node.querySelector(".bubble-user").textContent = text; chatLog.appendChild(node); bottom(); }
  function addLoading() { hideEmpty(); const node = loadingTpl.content.cloneNode(true); chatLog.appendChild(node); bottom(); return chatLog.querySelector(".msg-loading:last-of-type"); }
  function addError(text, canRetry) { hideEmpty(); const node = errorTpl.content.cloneNode(true); node.querySelector(".bubble-error-text").textContent = text; const retry = node.querySelector(".retry-btn"); if (canRetry) { retry.hidden = false; retry.addEventListener("click", () => send(lastMessage)); } chatLog.appendChild(node); bottom(); setSystemStatus("error"); setPipeline("error"); }
  function markdown(text) { if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(text || "")); const safe = (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); return `<p>${safe.replace(/\n/g, "<br>")}</p>`; }
  function shortThread(id) { return id ? `${id.slice(0, 8)}…` : "Mới"; }
  function formatAgents(agents) { return Array.isArray(agents) && agents.length ? ` · ${agents.join(" · ")}` : ""; }
  function setupTabs(bubble) { const buttons = bubble.querySelectorAll(".tab-btn"); buttons.forEach((button) => button.addEventListener("click", () => { buttons.forEach((item) => item.classList.remove("active")); button.classList.add("active"); bubble.querySelectorAll(".details-panel").forEach((panel) => { panel.hidden = panel.dataset.panel !== button.dataset.tab; }); })); }
  function displayValue(value) { return value && String(value).trim() ? String(value) : "Agent không trả về dữ liệu cho mục này."; }
  function applyDataStatus(data) { const state = data.status === "awaiting_approval" ? "approval" : data.status === "completed" ? "complete" : "ready"; setSystemStatus(state); setPipeline(state); }
  async function resumeApproval(approved, feedback, approvalBox) {
    if (sending || !threadId) return;
    sending = true; sendBtn.disabled = true; setSystemStatus("working"); setPipeline("working");
    try { const response = await fetch("/api/travel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ thread_id: threadId, approved, feedback }) }); const data = await response.json(); if (!response.ok || !data.success) { addError(data.error || "Không thể tiếp tục quy trình duyệt.", false); return; } approvalBox.hidden = true; addAi(data); }
    catch (error) { addError("Không thể kết nối tới máy chủ Render.", false); console.error(error); }
    finally { sending = false; sendBtn.disabled = false; }
  }
  function addAi(data) {
    hideEmpty(); threadId = data.thread_id || threadId; threadLabel.textContent = shortThread(threadId);
    const node = aiTpl.content.cloneNode(true); const bubble = node.querySelector(".bubble-ai"); const statusText = bubble.querySelector(".answer-status-text");
    statusText.textContent = data.status === "awaiting_approval" ? "Bản nháp chờ phê duyệt" : data.status === "completed" ? "Lịch trình cuối đã sẵn sàng" : "Kết quả từ supervisor";
    bubble.querySelector(".answer-time").textContent = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" }).format(new Date());
    bubble.querySelector(".answer-content").innerHTML = markdown(data.answer);
    const values = { flight: data.flight_results, hotel: data.hotel_results, weather: data.weather_result, budget: data.budget_results, itinerary: data.itinerary };
    Object.entries(values).forEach(([key, value]) => { bubble.querySelector(`[data-panel="${key}"]`).textContent = displayValue(value); });
    if (typeof data.llm_calls === "number") bubble.querySelector(".llm-calls").textContent = `${data.llm_calls} lần gọi AI`;
    bubble.querySelector(".agents-used").textContent = formatAgents(data.selected_agents); setupTabs(bubble);
    if (data.status === "awaiting_approval") { const box = bubble.querySelector(".approval-box"); box.hidden = false; box.querySelector(".approval-request").textContent = data.approval_request || "Vui lòng duyệt bản nháp trước khi tạo câu trả lời cuối."; box.querySelector(".approve-btn").addEventListener("click", () => resumeApproval(true, "", box)); box.querySelector(".revise-btn").addEventListener("click", () => { const feedback = window.prompt("Bạn muốn chỉnh sửa điều gì?", ""); if (feedback !== null && feedback.trim()) resumeApproval(false, feedback.trim(), box); }); }
    chatLog.appendChild(node); bottom(); applyDataStatus(data);
  }
  async function send(text) {
    if (sending || !text.trim()) return;
    lastMessage = text.trim(); sending = true; sendBtn.disabled = true; messageInput.disabled = true; addUser(lastMessage); addLoading(); setSystemStatus("working"); setPipeline("working"); const loading = chatLog.querySelector(".msg-loading:last-of-type");
    try { const response = await fetch("/api/travel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: lastMessage, thread_id: threadId }) }); const data = await response.json(); if (loading) loading.remove(); if (!response.ok || !data.success) { addError(data.error || "Đã có lỗi xảy ra khi lập kế hoạch.", true); return; } addAi(data); }
    catch (error) { if (loading) loading.remove(); addError("Không thể kết nối tới máy chủ. Vui lòng thử lại.", true); console.error(error); }
    finally { sending = false; sendBtn.disabled = false; messageInput.disabled = false; messageInput.focus(); }
  }
  chatForm.addEventListener("submit", (event) => { event.preventDefault(); const text = messageInput.value.trim(); if (!text) return; messageInput.value = ""; messageInput.style.height = "auto"; send(text); });
  messageInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); chatForm.requestSubmit(); } });
  messageInput.addEventListener("input", () => { messageInput.style.height = "auto"; messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px"; });
  document.querySelectorAll(".example-chip").forEach((chip) => chip.addEventListener("click", () => { messageInput.value = chip.querySelector("span:nth-child(2)").textContent; messageInput.focus(); messageInput.dispatchEvent(new Event("input")); }));
  newChatBtn.addEventListener("click", () => { threadId = null; lastMessage = ""; threadLabel.textContent = "Mới"; chatLog.innerHTML = ""; chatLog.appendChild(emptyState); emptyState.hidden = false; setSystemStatus("ready"); resetPipeline(); messageInput.focus(); });
  resetPipeline(); setSystemStatus("ready");
})();

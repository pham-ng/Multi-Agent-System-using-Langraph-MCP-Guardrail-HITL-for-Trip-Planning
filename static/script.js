(function () {
  "use strict";

  const chatLog = document.getElementById("chatLog");
  const emptyState = document.getElementById("emptyState");
  const chatForm = document.getElementById("chatForm");
  const messageInput = document.getElementById("messageInput");
  const sendBtn = document.getElementById("sendBtn");
  const newChatBtn = document.getElementById("newChatBtn");
  const refreshHistoryBtn = document.getElementById("refreshHistoryBtn");
  const threadList = document.getElementById("threadList");
  const threadLabel = document.getElementById("threadLabel");
  const systemStatus = document.getElementById("systemStatus");
  const systemStatusHint = document.getElementById("systemStatusHint");
  const userTpl = document.getElementById("userBubbleTpl");
  const aiTpl = document.getElementById("aiBubbleTpl");
  const loadingTpl = document.getElementById("loadingBubbleTpl");
  const errorTpl = document.getElementById("errorBubbleTpl");
  const authModal = document.getElementById("authModal");
  const authBtn = document.getElementById("authBtn");
  const authClose = document.getElementById("authClose");
  const authError = document.getElementById("authError");
  const loginForm = document.getElementById("loginForm");
  const signupForm = document.getElementById("signupForm");
  const loginModeBtn = document.getElementById("loginModeBtn");
  const signupModeBtn = document.getElementById("signupModeBtn");
  const tripActions = document.getElementById("tripActions");
  const inspector = document.getElementById("inspector");
  const drawerBackdrop = document.getElementById("drawerBackdrop");
  const historySidebar = document.getElementById("historySidebar");
  const historyBackdrop = document.getElementById("historyBackdrop");
  const historyCount = document.getElementById("historyCount");
  const THREAD_STORAGE_KEY = "tripmate.activeThreadId";

  let threadId = null;
  let sending = false;
  let lastMessage = "";
  let currentUser = null;
  const statusCopy = {
    ready: ["Sẵn sàng lập kế hoạch", "Các agent đang chờ yêu cầu của bạn"],
    working: ["Đang xử lý yêu cầu", "Guardrail và supervisor đang điều phối"],
    approval: ["Chờ bạn phê duyệt", "Bản nháp đã sẵn sàng để review"],
    complete: ["Lịch trình đã hoàn tất", "Bạn có thể bắt đầu chuyến đi"],
    error: ["Cần thử lại", "Kiểm tra kết nối hoặc cấu hình API"],
  };

  function bottom() { chatLog.scrollTop = chatLog.scrollHeight; }
  function setSystemStatus(state) {
    const copy = statusCopy[state] || statusCopy.ready;
    systemStatus.textContent = copy[0];
    systemStatusHint.textContent = copy[1];
    document.body.dataset.appState = state;
  }
  function hideEmpty() { if (emptyState) emptyState.hidden = true; }
  function resetPipeline() {
    document.querySelectorAll("[data-pipeline]").forEach((item) => { item.className = ""; });
  }
  function setPipeline(state) {
    const order = state === "working"
      ? ["guardrail", "supervisor", "data", "budget", "itinerary"]
      : state === "approval"
        ? ["guardrail", "supervisor", "data", "itinerary"]
        : state === "complete"
          ? ["guardrail", "supervisor", "data", "budget", "itinerary"]
          : [];
    document.querySelectorAll("[data-pipeline]").forEach((item) => {
      item.className = order.includes(item.dataset.pipeline) ? "done" : "";
    });
  }
  function shortThread(id) { return id ? `${id.slice(0, 8)}…` : "Mới"; }
  function formatAgents(agents) { return Array.isArray(agents) && agents.length ? ` · ${agents.join(" · ")}` : ""; }
  function displayValue(value) { return value && String(value).trim() ? String(value) : "Agent không trả về dữ liệu cho mục này."; }
  function setAuthModal(open) { authModal.hidden = !open; if (open) (loginForm.hidden ? signupForm : loginForm).querySelector("input")?.focus(); }
  function setAuthMode(mode) { const login = mode === "login"; loginForm.hidden = !login; signupForm.hidden = login; loginModeBtn.classList.toggle("active", login); signupModeBtn.classList.toggle("active", !login); authError.textContent = ""; }
  function setDrawer(open) { inspector.classList.toggle("drawer-open", open); drawerBackdrop.hidden = !open; }
  function setHistoryDrawer(open) { historySidebar.classList.toggle("drawer-open", open); historyBackdrop.hidden = !open; }
  function updateAuthUi() { authBtn.textContent = currentUser ? `Đăng xuất · ${currentUser.display_name || currentUser.email}` : "Đăng nhập"; }
  function updateTripActions() { tripActions.hidden = !threadId; }
  async function loadAuth() {
    try {
      const response = await fetch("/api/auth/me");
      const data = await response.json();
      currentUser = data.authenticated ? data.user : null;
      updateAuthUi();
      if (data.auth_required && !currentUser) setAuthModal(true);
      return data;
    } catch (error) { console.debug("Auth status unavailable", error); return null; }
  }
  async function authSubmit(form, endpoint) {
    authError.textContent = "";
    const payload = Object.fromEntries(new FormData(form).entries());
    try {
      const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || "Không thể xác thực.");
      currentUser = data.user; updateAuthUi(); setAuthModal(false); await loadThreads();
    } catch (error) { authError.textContent = error.message; }
  }
  function markdown(text) {
    if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(text || ""));
    const safe = (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<p>${safe.replace(/\n/g, "<br>")}</p>`;
  }
  function applyDataStatus(data) {
    const state = data.status === "awaiting_approval" ? "approval" : data.status === "completed" ? "complete" : "ready";
    setSystemStatus(state);
    setPipeline(state);
  }
  function persistThread() {
    if (threadId) window.localStorage.setItem(THREAD_STORAGE_KEY, threadId);
  }
  function clearConversation() {
    chatLog.innerHTML = "";
    chatLog.appendChild(emptyState);
    emptyState.hidden = false;
    threadId = null;
    lastMessage = "";
    threadLabel.textContent = "Mới";
  }
  function addUser(text) {
    hideEmpty();
    const node = userTpl.content.cloneNode(true);
    node.querySelector(".bubble-user").textContent = text;
    chatLog.appendChild(node);
    bottom();
  }
  function addLoading() {
    hideEmpty();
    const node = loadingTpl.content.cloneNode(true);
    chatLog.appendChild(node);
    bottom();
    return chatLog.querySelector(".msg-loading:last-of-type");
  }
  function addError(text, canRetry) {
    hideEmpty();
    const node = errorTpl.content.cloneNode(true);
    node.querySelector(".bubble-error-text").textContent = text;
    const retry = node.querySelector(".retry-btn");
    if (canRetry) {
      retry.hidden = false;
      retry.addEventListener("click", () => send(lastMessage));
    }
    chatLog.appendChild(node);
    bottom();
    setSystemStatus("error");
    setPipeline("error");
  }
  function setupTabs(bubble) {
    const buttons = bubble.querySelectorAll(".tab-btn");
    buttons.forEach((button) => button.addEventListener("click", () => {
      buttons.forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      bubble.querySelectorAll(".details-panel").forEach((panel) => {
        panel.hidden = panel.dataset.panel !== button.dataset.tab;
      });
    }));
  }
  async function resumeApproval(approved, feedback, approvalBox) {
    if (sending || !threadId) return;
    sending = true;
    sendBtn.disabled = true;
    setSystemStatus("working");
    setPipeline("working");
    try {
      const response = await fetch("/api/travel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, approved, feedback }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        addError(data.error || "Không thể tiếp tục quy trình duyệt.", false);
        return;
      }
      approvalBox.hidden = true;
      addAi(data);
    } catch (error) {
      addError("Không thể kết nối tới máy chủ Render.", false);
      console.error(error);
    } finally {
      sending = false;
      sendBtn.disabled = false;
    }
  }
  function addAi(data) {
    hideEmpty();
    threadId = data.thread_id || threadId;
    lastMessage = data.user_query || lastMessage;
    persistThread();
    updateTripActions();
    threadLabel.textContent = shortThread(threadId);
    const node = aiTpl.content.cloneNode(true);
    const bubble = node.querySelector(".bubble-ai");
    const statusText = bubble.querySelector(".answer-status-text");
    statusText.textContent = data.status === "awaiting_approval" ? "Bản nháp chờ phê duyệt" : data.status === "completed" ? "Lịch trình cuối đã sẵn sàng" : "Kết quả từ supervisor";
    bubble.querySelector(".answer-time").textContent = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" }).format(new Date());
    bubble.querySelector(".answer-content").innerHTML = markdown(data.answer);
    const values = { flight: data.flight_results, hotel: data.hotel_results, weather: data.weather_result, budget: data.budget_results, itinerary: data.itinerary, sources: (data.evidence_records || []).map((item) => `[${item.status}] ${item.source} · ${item.kind}\n${item.retrieved_at}\n${item.source_url || "Không có URL nguồn"}\n${item.content}`).join("\n\n") };
    Object.entries(values).forEach(([key, value]) => { bubble.querySelector(`[data-panel="${key}"]`).textContent = displayValue(value); });
    const groundingStatus = bubble.querySelector(".grounding-status");
    if (data.grounding_status === "verified") {
      groundingStatus.textContent = "✓ Đã kiểm chứng";
      groundingStatus.dataset.state = "verified";
    } else if (data.grounding_status === "blocked") {
      groundingStatus.textContent = "! Đã chặn vì thiếu bằng chứng";
      groundingStatus.dataset.state = "blocked";
    } else {
      groundingStatus.textContent = "Đang kiểm chứng";
      groundingStatus.dataset.state = "pending";
    }
    if (typeof data.llm_calls === "number") bubble.querySelector(".llm-calls").textContent = `${data.llm_calls} lần gọi AI`;
    bubble.querySelector(".agents-used").textContent = formatAgents(data.selected_agents);
    setupTabs(bubble);
    if (data.status === "awaiting_approval") {
      const box = bubble.querySelector(".approval-box");
      box.hidden = false;
      box.querySelector(".approval-request").textContent = data.approval_request || "Vui lòng duyệt bản nháp trước khi tạo câu trả lời cuối.";
      box.querySelector(".approve-btn").addEventListener("click", () => resumeApproval(true, "", box));
      box.querySelector(".revise-btn").addEventListener("click", () => {
        const feedback = window.prompt("Bạn muốn chỉnh sửa điều gì?", "");
        if (feedback !== null && feedback.trim()) resumeApproval(false, feedback.trim(), box);
      });
      box.querySelectorAll(".quick-feedback").forEach((button) => button.addEventListener("click", () => resumeApproval(false, button.dataset.feedback || "", box)));
    }
    chatLog.appendChild(node);
    bottom();
    applyDataStatus(data);
    loadThreads();
  }
  function renderThreads(threads) {
    threadList.replaceChildren();
    if (historyCount) historyCount.textContent = String(threads.length);
    if (!threads.length) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "Các phiên đã lưu sẽ xuất hiện ở đây.";
      threadList.appendChild(empty);
      return;
    }
    threads.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `thread-item${item.thread_id === threadId ? " active" : ""}`;
      const title = document.createElement("strong");
      title.textContent = item.title || "Trip planning session";
      const meta = document.createElement("small");
      meta.textContent = item.status === "awaiting_approval" ? "Chờ phê duyệt" : item.status === "completed" ? "Đã hoàn tất" : "Đang xử lý";
      const preview = document.createElement("span");
      preview.textContent = item.preview || item.user_query || "";
      button.append(title, meta, preview);
      button.addEventListener("click", () => loadThread(item.thread_id));
      threadList.appendChild(button);
    });
  }
  async function loadThreads() {
    try {
      const response = await fetch("/api/threads?limit=30");
      const data = await response.json();
      if (response.ok && data.success) renderThreads(data.threads || []);
    } catch (error) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "Không tải được lịch sử lúc này.";
      threadList.replaceChildren(empty);
      console.debug("History unavailable", error);
    }
  }
  async function loadThread(id) {
    if (!id || sending) return;
    try {
      const response = await fetch(`/api/threads/${encodeURIComponent(id)}`);
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || "Thread not found");
      clearConversation();
      threadId = data.thread_id;
      updateTripActions();
      lastMessage = data.user_query || "";
      persistThread();
      threadLabel.textContent = shortThread(threadId);
      if (lastMessage) addUser(lastMessage);
      addAi(data);
      await loadThreads();
    } catch (error) {
      addError("Không thể tải lại phiên này. Có thể database chưa sẵn sàng.", false);
      console.error(error);
    }
  }
  async function updateTrip(payload) {
    if (!threadId) return;
    const response = await fetch(`/api/threads/${encodeURIComponent(threadId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || "Không thể cập nhật chuyến đi.");
    await loadThreads();
  }
  async function exportTrip(format) {
    if (!threadId) return;
    const response = await fetch(`/api/threads/${encodeURIComponent(threadId)}/export?format=${format}`);
    if (!response.ok) throw new Error("Không thể xuất lịch trình.");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `tripmate-${threadId}.${format === "ics" ? "ics" : "md"}`; link.click(); URL.revokeObjectURL(url);
  }
  async function renameActiveTrip() {
    if (!threadId) return;
    const title = window.prompt("Tên chuyến đi", "Chuyến đi của tôi");
    if (title === null || !title.trim()) return;
    try { await updateTrip({ title: title.trim() }); } catch (error) { addError(error.message, false); }
  }
  async function shareActiveTrip() {
    if (!threadId) return;
    try {
      const response = await fetch(`/api/threads/${encodeURIComponent(threadId)}/share`, { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || "Không thể tạo liên kết chia sẻ.");
      await navigator.clipboard.writeText(data.url);
      setSystemStatus("complete"); systemStatusHint.textContent = "Đã sao chép liên kết chia sẻ (hết hạn sau 72 giờ)";
    } catch (error) { addError(error.message, false); }
  }
  async function send(text) {
    if (sending || !text.trim()) return;
    lastMessage = text.trim();
    sending = true;
    sendBtn.disabled = true;
    messageInput.disabled = true;
    addUser(lastMessage);
    const loading = addLoading();
    setSystemStatus("working");
    setPipeline("working");
    try {
      const response = await fetch("/api/travel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: lastMessage, thread_id: threadId }),
      });
      const data = await response.json();
      if (loading) loading.remove();
      if (!response.ok || !data.success) {
        if (response.status === 401 || data.code === "unauthenticated") { setAuthModal(true); return; }
        addError(data.error || "Đã có lỗi xảy ra khi lập kế hoạch.", true);
        return;
      }
      addAi(data);
    } catch (error) {
      if (loading) loading.remove();
      addError("Không thể kết nối tới máy chủ. Vui lòng thử lại.", true);
      console.error(error);
    } finally {
      sending = false;
      sendBtn.disabled = false;
      messageInput.disabled = false;
      messageInput.focus();
    }
  }

  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = messageInput.value.trim();
    if (!text) return;
    messageInput.value = "";
    messageInput.style.height = "auto";
    send(text);
  });
  messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });
  messageInput.addEventListener("input", () => {
    messageInput.style.height = "auto";
    messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px";
  });
  document.querySelectorAll(".example-chip").forEach((chip) => chip.addEventListener("click", () => {
    messageInput.value = chip.querySelector("span:nth-child(2)").textContent;
    messageInput.focus();
    messageInput.dispatchEvent(new Event("input"));
  }));
  newChatBtn.addEventListener("click", () => {
    window.localStorage.removeItem(THREAD_STORAGE_KEY);
    clearConversation();
    setSystemStatus("ready");
    resetPipeline();
    loadThreads();
    messageInput.focus();
  });
  document.getElementById("sidebarNewChatBtn")?.addEventListener("click", () => {
    window.localStorage.removeItem(THREAD_STORAGE_KEY);
    clearConversation();
    setSystemStatus("ready");
    resetPipeline();
    setHistoryDrawer(false);
    loadThreads();
    messageInput.focus();
  });
  refreshHistoryBtn?.addEventListener("click", loadThreads);
  authBtn.addEventListener("click", async () => {
    if (!currentUser) { setAuthMode("login"); setAuthModal(true); return; }
    await fetch("/api/auth/logout", { method: "POST" }); currentUser = null; updateAuthUi(); clearConversation(); loadThreads();
  });
  authClose.addEventListener("click", () => setAuthModal(false));
  loginModeBtn.addEventListener("click", () => setAuthMode("login"));
  signupModeBtn.addEventListener("click", () => setAuthMode("signup"));
  loginForm.addEventListener("submit", (event) => { event.preventDefault(); authSubmit(loginForm, "/api/auth/login"); });
  signupForm.addEventListener("submit", (event) => { event.preventDefault(); authSubmit(signupForm, "/api/auth/signup"); });
  document.getElementById("inspectorToggle").addEventListener("click", () => setDrawer(true));
  document.getElementById("inspectorClose").addEventListener("click", () => setDrawer(false));
  drawerBackdrop.addEventListener("click", () => setDrawer(false));
  document.getElementById("historyToggle")?.addEventListener("click", () => setHistoryDrawer(true));
  document.getElementById("historyClose")?.addEventListener("click", () => setHistoryDrawer(false));
  historyBackdrop?.addEventListener("click", () => setHistoryDrawer(false));
  document.getElementById("renameTripBtn").addEventListener("click", renameActiveTrip);
  document.getElementById("exportMdBtn").addEventListener("click", () => exportTrip("markdown").catch((error) => addError(error.message, false)));
  document.getElementById("exportIcsBtn").addEventListener("click", () => exportTrip("ics").catch((error) => addError(error.message, false)));
  document.getElementById("printTripBtn").addEventListener("click", () => window.print());
  document.getElementById("shareTripBtn").addEventListener("click", shareActiveTrip);

  resetPipeline();
  setSystemStatus("ready");
  loadAuth().then(() => loadThreads()).then(() => {
    const savedThread = window.localStorage.getItem(THREAD_STORAGE_KEY);
    if (savedThread) loadThread(savedThread);
  });
})();

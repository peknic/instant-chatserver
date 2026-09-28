"use strict";

/* ---------- 可调配置 ---------- */

// 每次打开聊天时默认渲染的最近消息条数；点击「加载更多」再往回取同样数量。
// 以后只改这一个常量即可调整默认显示数量。
const INITIAL_MESSAGE_LIMIT = 30;

// 断线后自动重连的最大次数，超过则提示手动重连。
const MAX_RECONNECT_ATTEMPTS = 3;

const STORAGE_KEYS = {
  token: "chatser_token",
  lastSeen: "chatser_last_seen",
};

const state = {
  token: null,
  userId: null,
  ws: null,
  connected: false,
  reconnectAttempts: 0,
  reconnectTimer: null,
  pingTimer: null,
  intentionalClose: false,
  chats: [],
  currentChat: null,
  currentChatName: "",
  unread: {},
  // chatId -> 已同步到的最大 message id（增量同步用，持久化到 localStorage）
  lastSeen: {},
  // 当前聊天窗口的分页状态：{chatId, offsetStart, total, hasMore, loading}
  messageView: null,
};

const el = (id) => document.getElementById(id);

function loadToken() {
  if (state.token) return state.token;
  try {
    return localStorage.getItem(STORAGE_KEYS.token);
  } catch (err) {
    return null;
  }
}

function clearToken() {
  state.token = null;
  try {
    localStorage.removeItem(STORAGE_KEYS.token);
  } catch (err) {
    /* ignore */
  }
}

function authHeaders(extra) {
  const headers = Object.assign({}, extra || {});
  const token = loadToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

function log(message, kind) {
  const box = el("log");
  const time = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  const span = document.createElement("span");
  span.className = "t";
  span.textContent = `[${time}] `;
  line.appendChild(span);
  const body = document.createElement("span");
  if (kind) body.className = kind;
  body.textContent = typeof message === "string" ? message : JSON.stringify(message);
  line.appendChild(body);
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

async function api(path, options) {
  const opts = Object.assign({}, options);
  opts.headers = authHeaders(opts.headers);
  const resp = await fetch(path, opts);
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (err) {
    data = text;
  }
  return { ok: resp.ok, status: resp.status, data };
}

/* ---------- 本地持久化（用户 ID / 每个 Chat 的最后同步 ID） ---------- */

function loadLastSeen() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.lastSeen);
    state.lastSeen = raw ? JSON.parse(raw) : {};
  } catch (err) {
    state.lastSeen = {};
  }
}

function saveLastSeen() {
  try {
    localStorage.setItem(STORAGE_KEYS.lastSeen, JSON.stringify(state.lastSeen));
  } catch (err) {
    /* ignore */
  }
}

function rememberLastSeen(chatId, messageId) {
  if (!chatId || !messageId) return;
  if (messageId > (state.lastSeen[chatId] || 0)) {
    state.lastSeen[chatId] = messageId;
    saveLastSeen();
  }
}

function setStatus(connected, text) {
  state.connected = connected;
  const node = el("conn-status");
  node.textContent = text;
  node.className = "status " + (connected ? "online" : "offline");
}

/* ---------- 注册 / 登录 ---------- */

let codeCooldownTimer = null;

function switchAuthTab(tab) {
  const isRegister = tab === "register";
  el("tab-register").classList.toggle("active", isRegister);
  el("tab-login").classList.toggle("active", !isRegister);
  el("register-form").classList.toggle("hidden", !isRegister);
  el("login-form").classList.toggle("hidden", isRegister);
}

function setAuthStatus(message, kind) {
  const node = el("auth-status");
  node.textContent = message || "";
  node.className = "hint" + (kind && kind !== "hint" ? " " + kind : "");
}

function storeToken(token) {
  state.token = token;
  try {
    localStorage.setItem(STORAGE_KEYS.token, token);
  } catch (err) {
    /* ignore */
  }
}

async function sendCode() {
  const email = el("reg-email").value.trim();
  const username = el("reg-username").value.trim();
  if (!email) {
    setAuthStatus("请先填写邮箱", "err");
    return;
  }
  const btn = el("send-code-btn");
  btn.disabled = true;
  setAuthStatus("发送中…");
  const { ok, status, data } = await api("/auth/send_code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username: username || null }),
  });
  if (ok) {
    const detail = data && data.message ? data.message : "验证码已发送";
    log(`验证码已发送到 ${email}`, "in");
    startCodeCooldown(30, detail);
  } else {
    const detail = (data && (data.detail || data.error)) || JSON.stringify(data);
    setAuthStatus(`发送失败 ${status}: ${detail}`, "err");
    btn.disabled = false;
  }
}

function startCodeCooldown(seconds, note) {
  const btn = el("send-code-btn");
  let remaining = seconds;
  btn.disabled = true;
  btn.textContent = `${remaining}s 后重发`;
  setAuthStatus(note || "");
  clearInterval(codeCooldownTimer);
  codeCooldownTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(codeCooldownTimer);
      btn.disabled = false;
      btn.textContent = "发送验证码";
    } else {
      btn.textContent = `${remaining}s 后重发`;
    }
  }, 1000);
}

function onAuthSuccess(data) {
  storeToken(data.token);
  const user = data.user;
  state.userId = user.id;
  setAuthStatus(`已登录：${user.username} (#${user.id})`, "ok");
  log(`登录成功 id=${user.id} username=${user.username}`, "in");
  // 切换账号：若已存在连接，先断开旧连接再建立新连接。
  if (state.ws) shutdown("切换登录用户，先断开旧连接");
  startConnect();
}

async function register() {
  const username = el("reg-username").value.trim();
  const email = el("reg-email").value.trim();
  const password = el("reg-password").value;
  const code = el("reg-code").value.trim();
  if (!username || !email || !password || !code) {
    setAuthStatus("请填写用户名、邮箱、密码和验证码", "err");
    return;
  }
  setAuthStatus("注册中…");
  const { ok, status, data } = await api("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password, code }),
  });
  if (ok) {
    onAuthSuccess(data);
  } else {
    const detail = (data && (data.detail || data.error)) || JSON.stringify(data);
    setAuthStatus(`注册失败 ${status}: ${detail}`, "err");
  }
}

async function login() {
  const account = el("login-account").value.trim();
  const password = el("login-password").value;
  if (!account || !password) {
    setAuthStatus("请填写账号和密码", "err");
    return;
  }
  setAuthStatus("登录中…");
  const { ok, status, data } = await api("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account, password }),
  });
  if (ok) {
    onAuthSuccess(data);
  } else {
    const detail = (data && (data.detail || data.error)) || JSON.stringify(data);
    setAuthStatus(`登录失败 ${status}: ${detail}`, "err");
  }
}

/* ---------- WebSocket（用户级，生命周期跟随页面） ---------- */

// 手动发起连接：重置重连计数并隐藏「重连」按钮。
function startConnect() {
  state.reconnectAttempts = 0;
  clearTimeout(state.reconnectTimer);
  hideReconnectButton();
  connect();
}

function showReconnectButton() {
  el("reconnect-btn").classList.remove("hidden");
}

function hideReconnectButton() {
  el("reconnect-btn").classList.add("hidden");
}

function connect() {
  const token = loadToken();
  if (!token) {
    log("请先登录", "err");
    return;
  }
  // 同一用户只保留一条 WebSocket，避免重复连接 / 重复监听。
  if (
    state.ws &&
    (state.ws.readyState === WebSocket.OPEN ||
      state.ws.readyState === WebSocket.CONNECTING)
  ) {
    log("WebSocket 已存在，忽略重复连接请求");
    return;
  }
  state.intentionalClose = false;

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/ws?token=${encodeURIComponent(token)}`;
  log(`连接 ${scheme}://${location.host}/ws`, "out");
  setStatus(false, "连接中…");

  const ws = new WebSocket(url);
  state.ws = ws;

  ws.onopen = async () => {
    if (state.ws !== ws) return;
    state.reconnectAttempts = 0;
    hideReconnectButton();
    setStatus(true, `已连接 User ${state.userId}`);
    log(`WebSocket 已连接 (user ${state.userId})`, "in");
    startPing();
    // 先用 REST 恢复聊天列表，再补齐断线期间新增的消息（WS 不负责任补发）。
    await refreshChats();
    await syncMissedMessages();
  };

  ws.onmessage = (event) => {
    if (state.ws !== ws) return;
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (err) {
      log(`收到非法 JSON: ${event.data}`, "err");
      return;
    }
    handleIncoming(payload);
  };

  ws.onclose = (event) => {
    if (state.ws !== ws) return;
    state.ws = null;
    stopPing();
    setStatus(false, "未连接");
    // 1008 = 策略拒绝（token 无效/过期）：清 token 并提示重新登录。
    if (event.code === 1008) {
      state.userId = null;
      clearToken();
      log("连接被拒绝：token 无效或已过期，请重新登录", "err");
      setAuthStatus("连接被拒绝：token 无效或已过期，请重新登录", "err");
      return;
    }
    log(`WebSocket 已断开 code=${event.code}`, "err");
    if (state.intentionalClose) return;
    if (state.reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      state.reconnectAttempts += 1;
      log(`2 秒后自动重连（第 ${state.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS} 次）…`);
      state.reconnectTimer = setTimeout(connect, 2000);
    } else {
      showReconnectButton();
      setStatus(false, "连接失败，请手动重连");
      log(`自动重连 ${MAX_RECONNECT_ATTEMPTS} 次仍失败，请点击「重连」`, "err");
      setAuthStatus(`连接失败：自动重连 ${MAX_RECONNECT_ATTEMPTS} 次未成功，请手动重连`, "err");
    }
  };

  ws.onerror = () => {
    log("WebSocket 错误（若为 1008，说明 token 无效或已过期）", "err");
  };
}

// 真正退出（关闭页面 / 软件）时主动断开。
function shutdown(reason) {
  state.intentionalClose = true;
  clearTimeout(state.reconnectTimer);
  stopPing();
  if (state.ws) {
    const ws = state.ws;
    state.ws = null;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    ws.onerror = null;
    try {
      ws.close();
    } catch (err) {
      /* ignore */
    }
  }
  setStatus(false, "未连接");
  log(reason || "已主动断开 WebSocket");
}

// 退出登录：断开 WebSocket、清空本地状态，之后需重新输入账号密码登录。
function logout() {
  shutdown("已退出登录");
  state.reconnectAttempts = 0;
  hideReconnectButton();
  state.userId = null;
  state.chats = [];
  state.unread = {};
  state.currentChat = null;
  state.currentChatName = "";
  state.messageView = null;
  closeChat();
  clearToken();
  setAuthStatus("已退出登录", "hint");
  log("已退出登录");
}

function startPing() {
  stopPing();
  state.pingTimer = setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 20000);
}

function stopPing() {
  clearInterval(state.pingTimer);
  state.pingTimer = null;
}

function handleIncoming(payload) {
  if (payload.type === "pong") {
    log("pong");
    return;
  }
  if (payload.type === "error") {
    log(`服务端错误: ${payload.detail}`, "err");
    return;
  }
  if (payload.type === "chat_invite") {
    const chat = payload.chat || {};
    log(`你被 user${payload.invited_by} 邀请加入 Chat #${payload.chat_id} members=[${(chat.members || []).join(",")}]`, "in");
    refreshChats();
    return;
  }
  if (payload.type === "chat_removed") {
    log(`你已被 user${payload.removed_by} 移出 Chat #${payload.chat_id}`, "err");
    delete state.lastSeen[payload.chat_id];
    saveLastSeen();
    if (state.currentChat && state.currentChat.id === payload.chat_id) {
      closeChat();
    }
    refreshChats();
    return;
  }
  if (payload.type === "chat_deleted") {
    log(`Chat #${payload.chat_id} 已被 user${payload.deleted_by} 删除`, "err");
    delete state.lastSeen[payload.chat_id];
    saveLastSeen();
    delete state.unread[payload.chat_id];
    if (state.currentChat && state.currentChat.id === payload.chat_id) {
      closeChat();
    }
    refreshChats();
    return;
  }
  if (payload.type === "message") {
    log(`收到消息 ${JSON.stringify(payload)}`, "in");
    rememberLastSeen(payload.chat_id, payload.id);
    if (state.currentChat && payload.chat_id === state.currentChat.id) {
      appendMessage(payload);
      if (state.messageView) state.messageView.total += 1;
    } else {
      state.unread[payload.chat_id] = (state.unread[payload.chat_id] || 0) + 1;
      renderChatList();
    }
  }
}

/* ---------- Chat 列表 ---------- */

async function refreshChats() {
  if (!state.userId) {
    log("请先连接用户", "err");
    return;
  }
  const { ok, status, data } = await api(`/chats`);
  if (!ok) {
    log(`获取 Chat 列表失败 ${status}: ${JSON.stringify(data)}`, "err");
    return;
  }
  state.chats = data;
  log(`Chat 列表已刷新，共 ${data.length} 个`, "in");
  renderChatList();
}

function renderChatList() {
  const list = el("chat-list");
  list.innerHTML = "";
  el("chat-list-empty").style.display = state.chats.length ? "none" : "block";

  state.chats.forEach((chat) => {
    const li = document.createElement("li");
    if (state.currentChat && chat.id === state.currentChat.id) li.classList.add("active");

    const left = document.createElement("span");
    const name = chat.name || `Chat #${chat.id}`;
    left.textContent = `${name}  `;
    const members = document.createElement("span");
    members.className = "meta";
    const admins = chat.adminmembers || [];
    members.textContent = admins.length
      ? `[${chat.members.join(",")}] admin:[${admins.join(",")}]`
      : `[${chat.members.join(",")}]`;
    left.appendChild(members);

    li.appendChild(left);

    const unread = state.unread[chat.id] || 0;
    if (unread > 0) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = unread;
      li.appendChild(badge);
    }

    li.addEventListener("click", () => openChat(chat.id));
    list.appendChild(li);
  });
}

async function createChat() {
  if (!state.userId) {
    log("请先连接用户", "err");
    return;
  }
  const members = el("chat-members")
    .value.split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => Number.isInteger(n) && n > 0);
  if (members.length < 2) {
    log("至少需要 2 个成员 ID", "err");
    return;
  }
  const name = el("chat-name").value.trim() || null;
  const { ok, status, data } = await api(`/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, members }),
  });
  if (ok) {
    log(`创建 Chat 成功 id=${data.id} members=[${data.members.join(",")}] admins=[${(data.adminmembers || []).join(",")}] (HTTP ${status})`, "in");
    el("chat-name").value = "";
    await refreshChats();
    openChat(data.id);
  } else {
    log(`创建 Chat 失败 ${status}: ${JSON.stringify(data)}`, "err");
  }
}

async function inviteMembers() {
  if (!state.userId) {
    log("请先连接用户", "err");
    return;
  }
  if (!state.currentChat) {
    log("请先进入一个 Chat", "err");
    return;
  }
  const members = el("invite-members")
    .value.split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => Number.isInteger(n) && n > 0);
  if (!members.length) {
    log("请输入要邀请的成员 ID", "err");
    return;
  }
  const chatId = state.currentChat.id;
  const { ok, status, data } = await api(
    `/chats/${chatId}/invite`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ members }),
    }
  );
  if (ok) {
    log(`邀请成功 Chat #${chatId} members=[${data.members.join(",")}]`, "in");
    el("invite-members").value = "";
    state.currentChat = data;
    el("chat-meta").textContent = `Chat #${data.id} | members: [${data.members.join(", ")}] | admins: [${(data.adminmembers || []).join(", ")}]`;
    await refreshChats();
  } else {
    const detail = (data && (data.detail || data.error)) || JSON.stringify(data);
    log(`邀请失败 ${status}: ${detail}`, "err");
  }
}

async function removeMember() {
  if (!state.userId) {
    log("请先连接用户", "err");
    return;
  }
  if (!state.currentChat) {
    log("请先进入一个 Chat", "err");
    return;
  }
  const memberId = parseInt(el("remove-member").value, 10);
  if (!Number.isInteger(memberId) || memberId < 1) {
    log("请输入要移出的成员 ID", "err");
    return;
  }
  const chatId = state.currentChat.id;
  const { ok, status, data } = await api(
    `/chats/${chatId}/members/${memberId}`,
    { method: "DELETE" }
  );
  if (ok) {
    log(`已移出 user${memberId}，Chat #${chatId} members=[${data.members.join(",")}]`, "in");
    el("remove-member").value = "";
    state.currentChat = data;
    el("chat-meta").textContent = `Chat #${data.id} | members: [${data.members.join(", ")}] | admins: [${(data.adminmembers || []).join(", ")}]`;
    await refreshChats();
  } else {
    const detail = (data && (data.detail || data.error)) || JSON.stringify(data);
    log(`移出失败 ${status}: ${detail}`, "err");
  }
}

async function deleteChat() {
  if (!state.userId) {
    log("请先连接用户", "err");
    return;
  }
  if (!state.currentChat) {
    log("请先进入要删除的 Chat", "err");
    return;
  }
  const chatId = state.currentChat.id;
  const name = state.currentChatName || `Chat #${chatId}`;
  if (!window.confirm(`确定删除「${name}」吗？该 Chat 及其所有聊天记录都会被永久清除。`)) {
    return;
  }
  const { ok, status, data } = await api(
    `/chats/${chatId}`,
    { method: "DELETE" }
  );
  if (ok) {
    log(`已删除 Chat #${chatId} 及其全部消息`, "in");
    delete state.lastSeen[chatId];
    saveLastSeen();
    delete state.unread[chatId];
    if (state.currentChat && state.currentChat.id === chatId) {
      closeChat();
    }
    await refreshChats();
  } else {
    const detail = (data && (data.detail || data.error)) || JSON.stringify(data);
    log(`删除 Chat 失败 ${status}: ${detail}`, "err");
  }
}

/* ---------- 进入 / 关闭 Chat ---------- */

async function openChat(chatId) {
  if (!state.userId) {
    log("请先连接用户", "err");
    return;
  }
  const { ok, status, data } = await api(`/chats/${chatId}`);
  if (!ok) {
    log(`进入 Chat #${chatId} 失败 ${status}: ${JSON.stringify(data)}`, "err");
    return;
  }
  state.currentChat = data;
  state.currentChatName = data.name || `Chat #${data.id}`;
  state.unread[data.id] = 0;
  state.messageView = {
    chatId: data.id,
    offsetStart: 0,
    total: 0,
    hasMore: false,
    loading: false,
  };

  el("chat-title").textContent = state.currentChatName;
  el("chat-meta").textContent = `Chat #${data.id} | members: [${data.members.join(", ")}]`;
  el("close-chat-btn").classList.remove("hidden");
  el("messages").innerHTML = "";
  updateLoadMore();
  renderChatList();

  await loadInitialMessages(data.id);
}

// 关闭聊天窗口：只清理窗口 UI，绝不触碰用户级 WebSocket。
function closeChat() {
  state.currentChat = null;
  state.currentChatName = "";
  state.messageView = null;
  el("chat-title").textContent = "未进入任何 Chat";
  el("chat-meta").textContent = "";
  el("messages").innerHTML = "";
  el("close-chat-btn").classList.add("hidden");
  updateLoadMore();
  renderChatList();
  log("已关闭 Chat 窗口（WebSocket 保持连接）");
}

/* ---------- 历史消息：默认最近 N 条 + 加载更多 ---------- */

async function loadInitialMessages(chatId) {
  const base = `/chats/${chatId}/messages`;
  // 现有接口为 id 升序 + offset：先取第一页拿到 total，再定位到最新的一页。
  const first = await api(`${base}?limit=${INITIAL_MESSAGE_LIMIT}&offset=0`);
  if (!first.ok) {
    log(`加载历史消息失败 ${first.status}: ${JSON.stringify(first.data)}`, "err");
    return;
  }
  const total = first.data.total;
  let items;
  let offsetStart;

  if (total <= INITIAL_MESSAGE_LIMIT) {
    items = first.data.items;
    offsetStart = 0;
  } else {
    offsetStart = total - INITIAL_MESSAGE_LIMIT;
    const resp = await api(`${base}?limit=${INITIAL_MESSAGE_LIMIT}&offset=${offsetStart}`);
    if (!resp.ok) {
      log(`加载历史消息失败 ${resp.status}: ${JSON.stringify(resp.data)}`, "err");
      return;
    }
    items = resp.data.items;
  }

  // 期间可能已切换 Chat，避免把旧数据渲染到新窗口。
  if (!state.currentChat || state.currentChat.id !== chatId) return;

  state.messageView = {
    chatId,
    offsetStart,
    total,
    hasMore: offsetStart > 0,
    loading: false,
  };
  const box = el("messages");
  box.innerHTML = "";
  items.forEach((msg) => appendMessage(msg));
  updateLoadMore();
  log(`Chat #${chatId} 载入最近 ${items.length}/${total} 条`, "in");
}

async function loadMoreMessages() {
  const view = state.messageView;
  if (!view || view.loading || !view.hasMore || !state.currentChat) return;
  view.loading = true;
  updateLoadMore(true);

  const chatId = view.chatId;
  const newStart = Math.max(0, view.offsetStart - INITIAL_MESSAGE_LIMIT);
  const fetchCount = view.offsetStart - newStart;
  const { ok, status, data } = await api(
    `/chats/${chatId}/messages?limit=${fetchCount}&offset=${newStart}`
  );

  view.loading = false;
  if (!ok) {
    log(`加载更多失败 ${status}: ${JSON.stringify(data)}`, "err");
    updateLoadMore();
    return;
  }
  if (!state.currentChat || state.currentChat.id !== chatId || state.messageView !== view) {
    return;
  }
  prependMessages(data.items);
  view.offsetStart = newStart;
  view.hasMore = newStart > 0;
  updateLoadMore();
  log(`已加载更早的 ${data.items.length} 条（offset=${newStart}）`, "in");
}

function updateLoadMore(loading) {
  const wrap = el("load-more-wrap");
  const btn = el("load-more-btn");
  const view = state.messageView;
  const isLoading = loading === undefined ? !!(view && view.loading) : loading;
  const hasMore = !!(view && view.hasMore);
  wrap.classList.toggle("hidden", !hasMore);
  btn.disabled = isLoading;
  btn.textContent = isLoading ? "加载中…" : "加载更多";
}

function buildMessageNode(msg) {
  const div = document.createElement("div");
  div.className = "msg" + (msg.sender_id === state.userId ? " mine" : "");
  div.dataset.id = msg.id;

  const head = document.createElement("span");
  head.className = "head";
  const time = msg.created_at ? new Date(msg.created_at).toLocaleTimeString() : "";
  head.textContent = `#${msg.id} user${msg.sender_id} · ${time}`;
  div.appendChild(head);

  const body = document.createElement("span");
  body.textContent = msg.content;
  div.appendChild(body);
  return div;
}

function appendMessage(msg) {
  const box = el("messages");
  if (box.querySelector(`[data-id="${msg.id}"]`)) return;
  box.appendChild(buildMessageNode(msg));
  box.scrollTop = box.scrollHeight;
  rememberLastSeen(msg.chat_id, msg.id);
}

// 把更早的消息插到列表顶部，保持原有顺序且不重复，并尽量保持滚动位置。
function prependMessages(items) {
  const box = el("messages");
  const frag = document.createDocumentFragment();
  const prevHeight = box.scrollHeight;
  const prevTop = box.scrollTop;
  items.forEach((msg) => {
    if (box.querySelector(`[data-id="${msg.id}"]`)) return;
    frag.appendChild(buildMessageNode(msg));
    rememberLastSeen(msg.chat_id, msg.id);
  });
  box.insertBefore(frag, box.firstChild);
  box.scrollTop = prevTop + (box.scrollHeight - prevHeight);
}

/* ---------- 离线消息同步（增量，基于 lastSeen message id） ---------- */

async function syncMissedMessages() {
  if (!state.chats.length) return;
  let changed = false;
  for (const chat of state.chats) {
    const lastId = state.lastSeen[chat.id];
    if (!lastId) continue;
    const missed = await fetchMessagesAfter(chat.id, lastId);
    if (!missed.length) continue;
    state.lastSeen[chat.id] = missed[missed.length - 1].id;
    changed = true;
    if (state.currentChat && state.currentChat.id === chat.id) {
      missed.forEach((msg) => appendMessage(msg));
    } else {
      state.unread[chat.id] = (state.unread[chat.id] || 0) + missed.length;
    }
    log(`Chat #${chat.id} 离线补齐 ${missed.length} 条新消息`, "in");
  }
  saveLastSeen();
  if (changed) renderChatList();
}

// 从最新消息向前翻页，收集所有 id > afterId 的消息（升序返回）。
async function fetchMessagesAfter(chatId, afterId) {
  const probe = await api(
    `/chats/${chatId}/messages?limit=1&offset=0`
  );
  if (!probe.ok) return [];
  const total = probe.data.total;
  let offset = Math.max(0, total - INITIAL_MESSAGE_LIMIT);
  const collected = [];

  while (true) {
    const { ok, data } = await api(
      `/chats/${chatId}/messages?limit=${INITIAL_MESSAGE_LIMIT}&offset=${offset}`
    );
    if (!ok) break;
    let reachedOld = false;
    for (let i = data.items.length - 1; i >= 0; i -= 1) {
      const msg = data.items[i];
      if (msg.id > afterId) {
        collected.push(msg);
      } else {
        reachedOld = true;
        break;
      }
    }
    if (reachedOld || offset === 0) break;
    offset = Math.max(0, offset - INITIAL_MESSAGE_LIMIT);
  }
  collected.reverse();
  return collected;
}

/* ---------- 发送消息 ---------- */

function sendMessage(event) {
  event.preventDefault();
  const input = el("message-input");
  const content = input.value.trim();
  if (!content) return;
  if (!state.currentChat) {
    log("请先进入一个 Chat", "err");
    return;
  }
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    log("WebSocket 未连接，无法发送", "err");
    return;
  }
  const payload = { type: "message", chat_id: state.currentChat.id, content };
  state.ws.send(JSON.stringify(payload));
  log(`发送 ${JSON.stringify(payload)}`, "out");
  input.value = "";
}

/* ---------- 事件绑定 ---------- */

el("logout-btn").addEventListener("click", logout);
el("reconnect-btn").addEventListener("click", () => {
  log("手动重连…");
  startConnect();
});
el("tab-register").addEventListener("click", () => switchAuthTab("register"));
el("tab-login").addEventListener("click", () => switchAuthTab("login"));
el("send-code-btn").addEventListener("click", sendCode);
el("register-btn").addEventListener("click", register);
el("login-btn").addEventListener("click", login);
el("create-chat-btn").addEventListener("click", createChat);
el("invite-btn").addEventListener("click", inviteMembers);
el("remove-member-btn").addEventListener("click", removeMember);
el("delete-chat-btn").addEventListener("click", deleteChat);
el("refresh-chats-btn").addEventListener("click", refreshChats);
el("load-more-btn").addEventListener("click", loadMoreMessages);
el("close-chat-btn").addEventListener("click", closeChat);
el("open-chat-btn").addEventListener("click", () => {
  const id = parseInt(el("open-chat-id").value, 10);
  if (id > 0) openChat(id);
});
el("send-form").addEventListener("submit", sendMessage);
el("clear-log-btn").addEventListener("click", () => {
  el("log").innerHTML = "";
});

// 页面/软件真正退出时主动关闭 WebSocket。
window.addEventListener("beforeunload", () => shutdown("页面关闭，主动断开 WebSocket"));
window.addEventListener("pagehide", (event) => {
  // persisted=true 表示进入 bfcache，页面可能被恢复，此时不永久关闭。
  if (!event.persisted) shutdown("页面隐藏，主动断开 WebSocket");
});

/* ---------- 启动：加载本地同步点，尝试用本地 token 恢复登录 ---------- */

async function restoreSession() {
  const token = loadToken();
  if (!token) {
    log("页面已就绪，请输入账号密码登录。");
    return;
  }
  const { ok, data } = await api("/auth/me");
  if (!ok || !data || !data.id) {
    clearToken();
    setAuthStatus("本地登录已失效，请重新登录", "err");
    log("本地 token 无效或已过期，请重新登录。", "err");
    return;
  }
  state.token = token;
  state.userId = data.id;
  setAuthStatus(`已登录：${data.username} (#${data.id})`, "ok");
  log(`已使用本地 token 恢复登录 id=${data.id} username=${data.username}`, "in");
  startConnect();
}

function bootstrap() {
  loadLastSeen();
  // 清理旧版遗留的“自动连接用户”键，避免用已删除的用户 ID 反复重连。
  try {
    localStorage.removeItem("chatser_user_id");
  } catch (err) {
    /* ignore */
  }
  restoreSession();
}

/* ---------- 访问密码门禁（前端遮罩，仅防误入，非安全边界） ---------- */

const ACCESS_PASSWORD = "159357";
const GATE_KEY = "chatser_unlocked";

function isUnlocked() {
  try {
    return sessionStorage.getItem(GATE_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function setupGate() {
  const gate = el("gate");
  const form = el("gate-form");
  const input = el("gate-password");
  const error = el("gate-error");

  if (isUnlocked()) {
    gate.classList.add("hidden");
    bootstrap();
    return;
  }

  input.focus();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value === ACCESS_PASSWORD) {
      try {
        sessionStorage.setItem(GATE_KEY, "1");
      } catch (err) {
        /* ignore */
      }
      gate.classList.add("hidden");
      bootstrap();
    } else {
      error.textContent = "密码错误，请重试";
      input.value = "";
      input.focus();
    }
  });
}

setupGate();

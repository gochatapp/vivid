// Vivid Chat Web UI - Main JavaScript
let currentBot = null;
let bots = [];

async function api(path, options = {}) {
    const response = await fetch(path, {
        headers: { "Content-Type": "application/json", ...options.headers },
        ...options
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({ error: "Request failed" }));
        throw new Error(error.error || `HTTP ${response.status}`);
    }
    return response.json();
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

async function loadBots() {
    try {
        const data = await api("/api/bots");
        bots = data.bots;
        renderBotsGrid();
        updateConnectionStatus(true);
    } catch (error) {
        console.error("Failed to load bots:", error);
        updateConnectionStatus(false);
        showError("Failed to load bots: " + error.message);
    }
}

function renderBotsGrid() {
    const grid = document.getElementById("bots-grid");
    if (!grid) return;
    if (bots.length === 0) {
        grid.innerHTML = "<div class=\"empty-state\" style=\"grid-column: 1 / -1;\"><h3>No bots running</h3><p>Start the bot with <code>python vivid.py</code></p></div>";
        return;
    }
    grid.innerHTML = bots.map(bot => "<div class=\"bot-card\"><h2>🤖 " + escapeHtml(bot.name) + "</h2><div class=\"bot-info\"><div><span class=\"label\">Provider</span><br><span class=\"value\">" + escapeHtml(bot.provider) + "</span></div><div><span class=\"label\">Model</span><br><span class=\"value\">" + escapeHtml(bot.model) + "</span></div><div><span class=\"label\">Role</span><br><span class=\"value\">" + escapeHtml(bot.role) + "</span></div><div><span class=\"label\">Persona</span><br><span class=\"value\">" + escapeHtml(bot.persona || "None") + "</span></div><div><span class=\"label\">Mode</span><br><span class=\"value " + (bot.mode === "story" ? "active" : "") + "\">" + escapeHtml(bot.mode) + "</span></div><div><span class=\"label\">Story</span><br><span class=\"value\">" + escapeHtml(bot.active_story || "None") + "</span></div><div><span class=\"label\">Messages</span><br><span class=\"value\">" + bot.total_messages_sent + "</span></div><div><span class=\"label\">History</span><br><span class=\"value\">" + bot.history_length + "</span></div></div></div>").join("");
}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById("connection-status");
    if (!statusEl) return;
    if (connected) { statusEl.classList.add("connected"); statusEl.querySelector("span:last-child").textContent = "Connected"; }
    else { statusEl.classList.remove("connected"); statusEl.querySelector("span:last-child").textContent = "Disconnected"; }
}

function showError(message) {
    const grid = document.getElementById("bots-grid");
    if (grid) { grid.innerHTML = "<div class=\"empty-state\" style=\"grid-column: 1 / -1;\"><h3>Error</h3><p>" + escapeHtml(message) + "</p></div>"; }
}

async function loadBotsForChat() {
    try { const data = await api("/api/bots"); bots = data.bots; renderBotSelect(); updateChatConnectionStatus(false); }
    catch (error) { console.error("Failed to load bots:", error); showChatError("Failed to load bots: " + error.message); }
}

function renderBotSelect() {
    const select = document.getElementById("bot-select");
    if (!select) return;
    select.innerHTML = "<option value=\"\">Select a bot...</option>" + bots.map(bot => "<option value=\"" + escapeHtml(bot.name) + "\">" + escapeHtml(bot.name) + "</option>").join("");
    if (currentBot) select.value = currentBot;
}

function updateChatConnectionStatus(connected) {
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    if (!dot || !text) return;
    if (connected) { dot.classList.add("connected"); text.textContent = "Connected"; }
    else { dot.classList.remove("connected"); text.textContent = "Disconnected"; }
}

async function switchBot() {
    const select = document.getElementById("bot-select");
    const botName = select.value;
    if (!botName) { currentBot = null; clearMessages(); updateChatConnectionStatus(false); hideQuickActions(); return; }
    currentBot = botName; updateChatConnectionStatus(true); clearMessages(); await loadBotHistory(); renderQuickActions();
}

async function loadBotHistory() {
    if (!currentBot) return;
    try { const data = await api("/api/bots/" + currentBot + "/history?limit=50"); data.history.forEach(msg => appendMessage(msg.role, msg.content, msg.is_command)); scrollToBottom(); }
    catch (error) { console.error("Failed to load history:", error); }
}

function renderQuickActions() {
    const container = document.getElementById("quick-actions");
    if (!container || !currentBot) return;
    const bot = bots.find(b => b.name === currentBot); if (!bot) return;
    const actions = [{ cmd: ".help", label: "Help" }, { cmd: ".status", label: "Status" }, { cmd: ".role", label: "Roles" }, { cmd: ".model", label: "Models" }, { cmd: ".provider", label: "Providers" }];
    if (bot.mode === "story") { actions.push({ cmd: ".story", label: "Story" }, { cmd: ".char", label: "Characters" }, { cmd: ".element", label: "Elements" }); }
    container.innerHTML = actions.map(a => "<button class=\"quick-btn\" onclick=\"sendCommand('" + a.cmd + "')\">" + a.label + "</button>").join("");
}

function hideQuickActions() { const container = document.getElementById("quick-actions"); if (container) container.innerHTML = ""; }

function appendMessage(role, content, isCommand = false) {
    const container = document.getElementById("messages"); if (!container) return;
    const msgDiv = document.createElement("div"); msgDiv.className = "message " + role;
    const roleLabels = { user: "You", assistant: "Bot", system: "System" };
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    msgDiv.innerHTML = "<div class=\"meta\"><span>" + (roleLabels[role] || role) + "</span><span>" + time + "</span>" + (isCommand ? "<span>🔧 Command</span>" : "") + "</div><div class=\"content\">" + escapeHtml(content) + "</div>";
    container.appendChild(msgDiv); scrollToBottom();
}

async function sendMessage() {
    const input = document.getElementById("message-input"); const text = input.value.trim(); if (!text || !currentBot) return;
    const isCommand = text.startsWith("."); appendMessage("user", text, isCommand); input.value = "";
    if (isCommand) await sendCommand(text); else await sendChatMessage(text);
}

async function sendChatMessage(message) {
    if (!currentBot) return;
    try { const data = await api("/api/bots/" + currentBot + "/message", { method: "POST", body: JSON.stringify({ message }) }); if (data.response) appendMessage("assistant", data.response); }
    catch (error) { console.error("Failed to send message:", error); appendMessage("system", "Error: " + error.message); }
}

async function sendCommand(command) {
    if (!currentBot) return;
    try { const data = await api("/api/bots/" + currentBot + "/command", { method: "POST", body: JSON.stringify({ command }) }); await loadBotHistory(); renderQuickActions(); }
    catch (error) { console.error("Command failed:", error); appendMessage("system", "Command error: " + error.message); }
}

function sendCommand(cmd) { const input = document.getElementById("message-input"); input.value = cmd; sendMessage(); }

function setupMessageInput() {
    const input = document.getElementById("message-input"); const sendBtn = document.getElementById("send-btn");
    if (input) { input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }); }
    if (sendBtn) sendBtn.addEventListener("click", sendMessage);
}

function clearMessages() { const container = document.getElementById("messages"); if (container) container.innerHTML = ""; }
function scrollToBottom() { const main = document.querySelector(".chat-main"); if (main) main.scrollTop = main.scrollHeight; }
function showChatError(message) { const container = document.getElementById("messages"); if (container) container.innerHTML = "<div class=\"message system\"><div class=\"content\">Error: " + escapeHtml(message) + "</div></div>"; }

document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("bots-grid")) { loadBots(); setInterval(loadBots, 10000); }
    if (document.getElementById("bot-select")) { loadBotsForChat(); setupMessageInput(); }
});

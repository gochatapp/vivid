// Vivid Chat Dashboard - Web UI
// Dashboard only - no chat interface (use Telegram for chat)

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
        grid.innerHTML = "<div class=\"empty-state\" style=\"grid-column: 1 / -1;\"><h3>No bots running</h3><p>Start the bot with <code>python -m system.runner</code></p></div>";
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

document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("bots-grid")) { 
        loadBots(); 
        setInterval(loadBots, 10000); 
    }
});

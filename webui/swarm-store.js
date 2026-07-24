import { createStore } from "/js/AlpineStore.js";
import { getNamespacedClient } from "/js/websocket.js";
import { callJsonApi } from "/js/api.js";
import { renderSafeMarkdown } from "/js/safe-markdown.js";

const EVT_SUB   = "swarm_subscribe";
const EVT_UNSUB = "swarm_unsubscribe";
const EVT_PUSH  = "swarm_push";

const POLL_INTERVAL_MS = 1500;
const ERROR_TEXT_LIMIT = 240;

const socket = getNamespacedClient("/ws");
socket.addHandlers(["ws_webui"]);

const proto = {
    agents: [],
    runs: [],
    selectedRunId: "",
    retryingMessageId: "",
    panelOpen: true,
    composingFor: null,
    composeText: "",
    expandedFor: {},
    connectionState: "idle",
    lastUpdatedAt: "",
    lastError: "",
    loading: false,
    _parentCtxId: "",
    _subscribedParent: "",
    _mountCount: 0,
    _pollTimer: null,
    _pollPromise: null,
    _contextSwitchToken: 0,
    _wsBound: false,
    _pushHandler: null,

    async onMount() {
        this._mountCount += 1;
        if (this._mountCount > 1) {
            await this._syncParentContext();
            await this._poll();
            return;
        }

        this.connectionState = "connecting";
        void this._bindSocket();
        await this._syncParentContext(true);
        await this._poll();

        if (!this._pollTimer) {
            this._pollTimer = setInterval(() => {
                void this._poll();
            }, POLL_INTERVAL_MS);
        }
    },

    cleanup() {
        this._mountCount = Math.max(0, this._mountCount - 1);
        if (this._mountCount > 0) return;

        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
        if (this._subscribedParent) {
            void socket.emit(EVT_UNSUB, {}).catch(() => {});
            this._subscribedParent = "";
        }
        this.connectionState = "idle";
    },

    async _bindSocket() {
        if (this._wsBound) return;
        this._wsBound = true;

        this._pushHandler = (envelope) => {
            if (this._mountCount < 1) return;
            if (this._readParentCtxId() !== this._parentCtxId) {
                void this._syncParentContext();
                return;
            }
            const payload = envelope?.data || {};
            this._applySnapshot(payload, this._parentCtxId, "live");
        };

        socket.onConnect(() => {
            if (this._mountCount < 1) return;
            this.connectionState = "connecting";
            void this._subscribe(this._parentCtxId);
        });
        socket.onDisconnect(() => {
            if (this._mountCount < 1) return;
            this.connectionState = "polling";
        });
        socket.onError((error) => {
            if (this._mountCount < 1) return;
            this.connectionState = "polling";
            this._setError(error, "Live updates unavailable");
        });

        try {
            await socket.on(EVT_PUSH, this._pushHandler);
        } catch (error) {
            this.connectionState = "polling";
            this._setError(error, "Live updates unavailable");
        }
    },

    _readParentCtxId() {
        try {
            return (typeof globalThis.getContext === "function" && globalThis.getContext()) || "";
        } catch (_error) {
            return "";
        }
    },

    async _syncParentContext(force = false) {
        const nextParent = this._readParentCtxId();
        if (!force && nextParent === this._parentCtxId) return;

        const token = ++this._contextSwitchToken;
        const hadSubscription = Boolean(this._subscribedParent);
        this._parentCtxId = nextParent;
        this._subscribedParent = "";
        this.runs = [];
        this.agents = [];
        this.selectedRunId = "";
        this.expandedFor = {};
        this.lastError = "";
        this.lastUpdatedAt = "";
        this.loading = Boolean(nextParent);
        this.connectionState = nextParent ? "connecting" : "waiting";

        if (hadSubscription) {
            void socket.emit(EVT_UNSUB, {}).catch(() => {});
        }

        if (token !== this._contextSwitchToken || !nextParent) return;
        void this._subscribe(nextParent);
    },

    async _subscribe(parent) {
        if (!parent || parent !== this._parentCtxId || this._mountCount < 1) return;
        try {
            await socket.emit(EVT_SUB, { parent_context_id: parent });
            if (parent !== this._parentCtxId) {
                void this._subscribe(this._parentCtxId);
                return;
            }
            this._subscribedParent = parent;
            this.connectionState = "live";
        } catch (error) {
            if (parent !== this._parentCtxId) return;
            this.connectionState = "polling";
            this._setError(error, "Live updates unavailable");
        }
    },

    async _poll() {
        if (this._pollPromise) return this._pollPromise;

        this._pollPromise = (async () => {
            await this._syncParentContext();
            const parent = this._parentCtxId;
            if (!parent) {
                this.loading = false;
                return;
            }

            try {
                const response = await callJsonApi("/plugins/a0_swarm/swarm_status", {
                    parent_context_id: parent,
                });
                if (this._readParentCtxId() !== parent) {
                    await this._syncParentContext();
                    return;
                }
                if (parent !== this._parentCtxId) return;
                if (!this._applySnapshot(response, parent, socket.isConnected() ? "live" : "polling")) {
                    throw new Error("Received a stale or malformed swarm snapshot");
                }
            } catch (error) {
                if (parent !== this._parentCtxId) return;
                this.loading = false;
                this.connectionState = "error";
                this._setError(error, "Could not refresh swarm status");
            }
        })();

        try {
            await this._pollPromise;
        } finally {
            this._pollPromise = null;
        }
    },

    _applySnapshot(data, expectedParent, source) {
        if (!data || data.parent_context_id !== expectedParent) return false;
        if (!Array.isArray(data.runs) || !Array.isArray(data.agents)) return false;
        if (expectedParent !== this._parentCtxId) return false;

        this.runs = data.runs;
        this.agents = data.agents;
        this.loading = false;
        this.lastError = "";
        this.lastUpdatedAt = new Date().toISOString();
        this.connectionState = source;
        this._ensureSelectedRun();
        return true;
    },

    _setError(error, prefix) {
        const detail = String(error?.message || error || "Unknown error")
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, ERROR_TEXT_LIMIT);
        this.lastError = detail ? `${prefix}: ${detail}` : prefix;
    },

    _ensureSelectedRun() {
        if (!this.runs.length) {
            this.selectedRunId = "";
            return;
        }
        if (!this.selectedRunId || !this.runs.some(r => r.run_id === this.selectedRunId)) {
            this.selectedRunId = this.runs[0].run_id;
        }
    },

    get selectedRun() {
        return this.runs.find(r => r.run_id === this.selectedRunId) || this.runs[0] || null;
    },

    runAgents(run) {
        return (run && Array.isArray(run.agents)) ? run.agents : [];
    },

    runMessages(run) {
        return (run && Array.isArray(run.messages)) ? run.messages : [];
    },

    runTimeline(run) {
        return (run && Array.isArray(run.timeline)) ? run.timeline : [];
    },

    deliveryClass(state) {
        return "delivery-" + (state || "queued");
    },

    totalTokens(agent) {
        return (Number(agent?.input_tokens) || 0) + (Number(agent?.output_tokens) || 0);
    },

    agentOrigin(agent) {
        if (agent && agent.is_remote) return agent.remote_label || agent.remote_base_url || "remote";
        return "local";
    },

    messageFailure(message) {
        return (message && message.failure_reason) || "";
    },

    async retryMessage(messageId) {
        if (!messageId) return;
        const parent = this._parentCtxId;
        if (!parent) return;
        this.retryingMessageId = messageId;
        try {
            const response = await callJsonApi("/plugins/a0_swarm/swarm_retry_message", {
                parent_context_id: parent,
                message_id: messageId,
            });
            if (response?.ok === false) throw new Error(response.error || "Retry failed");
        } catch (error) {
            this._setError(error, "Could not retry message");
        } finally {
            this.retryingMessageId = "";
            await this._poll();
        }
    },

    get activeAgents() {
        return this.agents.filter(a => !["done", "failed", "cancelled"].includes(a.status));
    },
    get completedAgents() {
        return this.agents.filter(a => ["done", "failed", "cancelled"].includes(a.status));
    },
    get hasAgents() { return this.agents.length > 0; },
    get hasParentContext() { return Boolean(this._parentCtxId); },

    statusClass(s) { return "status-" + s; },
    unreadCount(a) {
        return (a.messages || []).filter(m => !m.read && m.sender !== "orchestrator").length;
    },

    isExpanded(name) { return !!this.expandedFor[name]; },
    toggleExpand(name) {
        this.expandedFor = { ...this.expandedFor, [name]: !this.expandedFor[name] };
        if (this.expandedFor[name]) this.markRead(name);
    },

    /**
     * Derive a 1-8 slot index from agent_name "SA{parent}_{slot}-{run}".
     * Falls back to a stable hash if the name doesn't match.
     */
    slotIndex(name) {
        const m = /^SA\d+_(\d+)(?:-|$)/.exec(name || "");
        if (m) return ((parseInt(m[1], 10) - 1) % 8) + 1;
        let h = 0;
        for (let i = 0; i < (name || "").length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
        return (h % 8) + 1;
    },

    /**
     * Material-symbols glyph per slot. Distinct, role-evocative, not
     * status-coloured (status is shown by the pill + progress bar).
     */
    slotIcon(name) {
        const icons = [
            "psychology",        // 1 — cognition / planner
            "science",           // 2 — analysis
            "engineering",       // 3 — builder
            "auto_stories",      // 4 — researcher / reading
            "terminal",          // 5 — code
            "support_agent",     // 6 — helper
            "troubleshoot",      // 7 — debugger
            "inventory_2",       // 8 — generalist
        ];
        return icons[this.slotIndex(name) - 1] || "smart_toy";
    },

    openCompose(name)  { this.composingFor = name; this.composeText = ""; if (!this.expandedFor[name]) this.toggleExpand(name); },
    closeCompose()     { this.composingFor = null; this.composeText = ""; },
    togglePanel()      { this.panelOpen = !this.panelOpen; },
    markRead(name) {
        const a = this.agents.find(x => x.agent_name === name);
        if (a) (a.messages || []).forEach(m => m.read = true);
    },

    async sendMessage(name, unblock = false) {
        const content = (this.composeText || "").trim();
        const parent = this._parentCtxId;
        if (!content || !parent) return;
        this.closeCompose();
        try {
            const response = await callJsonApi("/plugins/a0_swarm/swarm_send_message", {
                parent_context_id: parent,
                agent_name: name,
                content,
                unblock,
            });
            if (response?.ok === false) throw new Error(response.error || "Send failed");
        } catch (error) {
            this._setError(error, "Could not send swarm message");
        }
        await this._poll();
    },

    async cancelAgent(name) {
        const parent = this._parentCtxId;
        if (!parent) return;
        try {
            const response = await callJsonApi("/plugins/a0_swarm/swarm_cancel", {
                parent_context_id: parent,
                agent_name: name,
            });
            if (response?.ok === false) throw new Error(response.error || "Cancel failed");
        } catch (error) {
            this._setError(error, "Could not cancel subagent");
        }
        await this._poll();
    },

    async clearCompleted() {
        const parent = this._parentCtxId;
        if (!parent) return;
        try {
            const response = await callJsonApi("/plugins/a0_swarm/swarm_clear_completed", {
                parent_context_id: parent,
            });
            if (response?.ok === false) throw new Error(response.error || "Clear failed");
        } catch (error) {
            this._setError(error, "Could not clear completed runs");
        }
        await this._poll();
    },

    get connectionLabel() {
        const labels = {
            idle: "idle",
            waiting: "waiting for chat",
            connecting: "connecting",
            live: "live",
            polling: "polling fallback",
            error: "refresh failed",
        };
        return labels[this.connectionState] || this.connectionState;
    },

    get lastUpdatedLabel() {
        if (!this.lastUpdatedAt) return "";
        return this.relativeTime(this.lastUpdatedAt);
    },

    /**
     * Render markdown to sanitized HTML for use with x-html.
     *
     * Post-processing: marked treats lists with blank lines between
     * items as "loose" and wraps each <li> body in a <p>, which
     * inflates vertical spacing past anything CSS can fix without
     * fighting the cascade. We unwrap those single-<p>-only list
     * items in the parsed DOM before returning the HTML, so the
     * output behaves like a tight list.
     */
    renderMd(text) {
        if (!text) return "";
        try {
            const html = renderSafeMarkdown(text);
            const tmpl = document.createElement("template");
            tmpl.innerHTML = html;
            for (const li of tmpl.content.querySelectorAll("li")) {
                // If the <li>'s element children are exactly one <p> (text
                // nodes around it OK), promote the <p>'s children up.
                const elementChildren = Array.from(li.children);
                if (elementChildren.length === 1 && elementChildren[0].tagName === "P") {
                    const p = elementChildren[0];
                    while (p.firstChild) p.parentNode.insertBefore(p.firstChild, p);
                    p.remove();
                }
            }
            return tmpl.innerHTML;
        } catch (_) {
            try { return renderSafeMarkdown(text); } catch (_) { return ""; }
        }
    },

    relativeTime(iso) {
        if (!iso) return "";
        const d = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
        if (d < 60)   return `${d}s ago`;
        if (d < 3600) return `${Math.floor(d/60)}m ago`;
        return `${Math.floor(d/3600)}h ago`;
    },

    elapsed(agent) {
        if (!agent || !agent.started_at) return "";
        const end = agent.finished_at ? new Date(agent.finished_at).getTime() : Date.now();
        const start = new Date(agent.started_at).getTime();
        const seconds = Math.max(0, Math.floor((end - start) / 1000));
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
        const hours = Math.floor(minutes / 60);
        return `${hours}h ${minutes % 60}m`;
    },

    formatNumber(value) {
        return new Intl.NumberFormat().format(Number(value) || 0);
    },
};

export const swarmStore = createStore("swarmStore", proto);

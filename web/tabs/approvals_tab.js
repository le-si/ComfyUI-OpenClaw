import { moltbotApi } from "../openclaw_api.js";
import { showError, clearError } from "../openclaw_utils.js";

// Helper for safe HTML escaping
function escapeHtml(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

export const ApprovalsTab = {
    id: "approvals",
    title: "Approvals",
    icon: "pi pi-check-circle",

    render(container) {
        // --- 1. Static Layout ---
        container.innerHTML = `
            <div class="moltbot-panel">
                <div class="moltbot-card" style="border-radius:0; border:none; border-bottom:1px solid var(--moltbot-color-border);">
                     <div class="moltbot-section-header">Approval Requests</div>
                     <div class="moltbot-error-box" style="display:none"></div>
                     <div class="moltbot-toolbar" style="margin-top:5px; display:flex; gap:5px; align-items:center;" id="apr-toolbar">
                        <div id="apr-filter-btns" style="display: flex; gap: 5px;">
                            <button class="moltbot-btn moltbot-btn-primary" data-status="pending">Pending</button>
                            <button class="moltbot-btn" data-status="approved">Approved</button>
                            <button class="moltbot-btn" data-status="rejected">Rejected</button>
                            <button class="moltbot-btn" data-status="">All</button>
                        </div>
                        <button class="moltbot-btn moltbot-btn-sm" id="apr-refresh-btn" style="margin-left: auto;">
                            Refresh
                        </button>
                    </div>
                </div>

                <div id="apr-list" class="moltbot-scroll-area" style="padding:0;">
                     <div class="moltbot-empty-state">Loading...</div>
                </div>
            </div>

            <!-- Details Modal -->
             <div id="apr-editor-overlay" class="moltbot-modal-overlay" style="display:none;">
                <div id="apr-details-modal" class="moltbot-modal" style="width: 600px;">
                    <div class="moltbot-modal-header">
                        <span id="apr-modal-title">Request Details</span>
                    </div>

                    <div class="moltbot-modal-body">
                         <div style="font-family: var(--moltbot-font-mono); font-size: var(--moltbot-font-xs); background: #111; padding: 10px; border: 1px solid #333; height: 300px; overflow: auto; white-space: pre-wrap;" id="apr-modal-content"></div>
                    </div>

                    <div class="moltbot-modal-footer">
                        <button class="moltbot-btn" id="apr-modal-close">Close</button>
                        <div id="apr-modal-actions" style="display:flex; gap:10px;"></div>
                    </div>
                </div>
            </div>
        `;

        // --- 2. State & References ---
        const ui = {
            list: container.querySelector("#apr-list"),
            filters: container.querySelector("#apr-filter-btns"),
            refreshBtn: container.querySelector("#apr-refresh-btn"),
            modal: {
                overlay: container.querySelector("#apr-editor-overlay"),
                el: container.querySelector("#apr-details-modal"),
                title: container.querySelector("#apr-modal-title"),
                content: container.querySelector("#apr-modal-content"),
                close: container.querySelector("#apr-modal-close"),
                actions: container.querySelector("#apr-modal-actions"),
            }
        };

        let currentState = {
            status: "pending",
            approvals: []
        };

        // --- 3. View Logic ---

        const renderInputsSummary = (inputs) => {
            if (!inputs) return "";
            const keys = Object.keys(inputs);
            if (keys.length === 0) return "No inputs";
            // Show first 2 inputs
            const firstTwo = keys.slice(0, 2).map(k => `${k}: ${stringifyVal(inputs[k])}`);
            if (keys.length > 2) firstTwo.push(`+${keys.length - 2} more`);
            return escapeHtml(firstTwo.join(", "));
        };

        const stringifyVal = (v) => {
            if (typeof v === "object") return "{...}";
            return String(v);
        }

        const renderListItem = (req) => {
            // Mapping status to CSS classes is cleaner than inline colors
            const statusClass = req.status; // pending, approved, rejected
            const isPending = req.status === "pending";

            return `
                <div class="moltbot-list-item" style="padding: 10px; border-bottom: 1px solid var(--moltbot-color-border); margin-bottom: 4px; border-left: 3px solid var(--moltbot-color-border);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div>
                            <div style="font-weight: bold; font-size: var(--moltbot-font-md); color: var(--moltbot-color-fg);">
                                ${escapeHtml(req.template_id)}
                                <span style="font-size: var(--moltbot-font-xs); font-weight: normal; color: var(--moltbot-color-fg-muted); margin-left:8px;">${escapeHtml(req.approval_id)}</span>
                            </div>
                            <div style="font-size: var(--moltbot-font-sm); color: var(--moltbot-color-fg-muted); margin-top: 4px;">
                                Inputs: <span style="color: #aaa;">${renderInputsSummary(req.inputs)}</span>
                            </div>
                            <div style="font-size: var(--moltbot-font-xs); color: #666; margin-top: 4px;">
                                Requested: ${new Date(req.requested_at).toLocaleString()}
                                ${req.source ? `via ${escapeHtml(req.source)}` : ''}
                            </div>
                        </div>
                        <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 5px;">
                            <span class="moltbot-badge ${statusClass}">
                                ${req.status.toUpperCase()}
                            </span>

                            <div style="display: flex; gap: 5px; margin-top: 5px;">
                                <button class="moltbot-btn moltbot-btn-sm" data-action="details" data-id="${req.approval_id}">Details</button>
                                ${isPending ? `
                                    <button class="moltbot-btn moltbot-btn-sm moltbot-btn-primary" data-action="approve" data-id="${req.approval_id}">Approve</button>
                                    <button class="moltbot-btn moltbot-btn-sm moltbot-btn-danger" data-action="reject" data-id="${req.approval_id}">Reject</button>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        };

        const renderList = () => {
            if (currentState.approvals.length === 0) {
                ui.list.innerHTML = '<div class="moltbot-empty-state">No requests found.</div>';
                return;
            }
            ui.list.innerHTML = currentState.approvals.map(renderListItem).join("");
        };

        // --- 4. Logic ---

        const loadApprovals = async () => {
            clearError(container);
            ui.list.innerHTML = '<div style="padding: 10px; text-align: center;">Loading...</div>';

            const params = { limit: 100 };
            if (currentState.status) params.status = currentState.status;

            const res = await moltbotApi.getApprovals(params);
            if (res.ok) {
                currentState.approvals = res.data.approvals || [];
                renderList();
            } else {
                ui.list.innerHTML = ''; // Clear loading
                showError(container, res.error);
            }
        };

        const handleApprove = async (id) => {
            if (!confirm("Approve this request? It will be executed immediately.")) return;

            const res = await moltbotApi.approveRequest(id, { autoExecute: true });
            if (res.ok) {
                alert(`Approved! ` + (res.data.executed ? `Executed as prompt ${res.data.prompt_id}` : `Marked approved.`));
                loadApprovals();
            } else {
                showError(container, `Approval failed: ${res.error}`);
            }
        };

        const handleReject = async (id) => {
            if (!confirm("Reject this request?")) return;

            const res = await moltbotApi.rejectRequest(id);
            if (res.ok) {
                loadApprovals();
            } else {
                showError(container, `Rejection failed: ${res.error}`);
            }
        };

        const showDetails = async (id) => {
            // Find in local state first, or fetch
            let req = currentState.approvals.find(a => a.approval_id === id);

            if (!req) {
                const res = await moltbotApi.getApproval(id);
                if (res.ok) req = res.data.approval;
            }

            if (!req) return;

            ui.modal.content.textContent = JSON.stringify(req, null, 2);

            // Render actions
            if (req.status === "pending") {
                ui.modal.actions.innerHTML = `
                    <button class="moltbot-btn moltbot-btn-primary" id="apr-modal-approve">Approve</button>
                    <button class="moltbot-btn moltbot-btn-danger" id="apr-modal-reject">Reject</button>
                `;

                // Bind dynamic buttons
                container.querySelector("#apr-modal-approve").onclick = () => { handleApprove(id); closeModal(); };
                container.querySelector("#apr-modal-reject").onclick = () => { handleReject(id); closeModal(); };

            } else {
                ui.modal.actions.innerHTML = "";
            }

            openModal();
        };

        const openModal = () => {
            ui.modal.overlay.style.display = "flex";
        };

        const closeModal = () => {
            ui.modal.overlay.style.display = "none";
        };

        // --- 5. Event Binding ---

        // Filters
        ui.filters.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-status]");
            if (btn && btn.parentElement === ui.filters) { // Ensure strict match
                // Update active state
                ui.filters.querySelectorAll("button").forEach(b => b.classList.remove("moltbot-btn-primary"));
                btn.classList.add("moltbot-btn-primary");

                // Update state
                currentState.status = btn.dataset.status; // "" for all
                loadApprovals();
            }
        });

        // Refresh
        ui.refreshBtn.addEventListener("click", loadApprovals);

        // List Actions
        ui.list.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-action]");
            if (!btn) return;

            const action = btn.dataset.action;
            const id = btn.dataset.id;

            if (action === "approve") handleApprove(id);
            else if (action === "reject") handleReject(id);
            else if (action === "details") showDetails(id);
        });

        // Modal
        ui.modal.close.addEventListener("click", closeModal);
        ui.modal.overlay.addEventListener("click", (e) => {
            if (e.target === ui.modal.overlay) closeModal();
        });

        // Initial Load
        loadApprovals();
    }
};

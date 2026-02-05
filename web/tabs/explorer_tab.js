
import { moltbotApi } from "../openclaw_api.js";
import { makeEl, showToast } from "../openclaw_ui.js";

/**
 * F28: Explorer Tab
 * Visualizes available specific Nodes and Models to help debugging.
 * Includes R42 Preflight Diagnostics and R47 Workflow Checkpoints.
 */
export const ExplorerTab = {
    id: "explorer",
    title: "Explorer",
    icon: "pi pi-compass",
    render: async (container) => {
        container.innerHTML = "";

        // Layout: Sidebar (Inventory/Snapshots) + Main (Preflight/Details)
        const layout = makeEl("div", "moltbot-explorer-layout");
        layout.style.display = "flex";
        layout.style.height = "100%";
        layout.style.gap = "1rem";

        const leftPanel = makeEl("div", "moltbot-explorer-sidebar");
        leftPanel.style.flex = "1";
        leftPanel.style.display = "flex";
        leftPanel.style.flexDirection = "column";

        const rightPanel = makeEl("div", "moltbot-explorer-main");
        rightPanel.style.flex = "2";
        rightPanel.style.display = "flex";
        rightPanel.style.flexDirection = "column";

        layout.appendChild(leftPanel);
        layout.appendChild(rightPanel);
        container.appendChild(layout);

        // --- Left Panel Tabs ---
        const leftTabs = makeEl("div", "moltbot-subtabs");
        leftTabs.style.display = "flex";
        leftTabs.style.gap = "10px";
        leftTabs.style.marginBottom = "10px";

        const tabInv = makeEl("button", "moltbot-btn active", "Inventory");
        const tabSnaps = makeEl("button", "moltbot-btn", "Snapshots");
        leftTabs.appendChild(tabInv);
        leftTabs.appendChild(tabSnaps);
        leftPanel.appendChild(leftTabs);

        // Content Areas
        const invContent = makeEl("div", "moltbot-tab-content active");
        invContent.style.flex = "1";
        invContent.style.display = "flex";
        invContent.style.flexDirection = "column";

        const snapsContent = makeEl("div", "moltbot-tab-content");
        snapsContent.style.flex = "1";
        snapsContent.style.display = "none";
        snapsContent.style.flexDirection = "column";

        leftPanel.appendChild(invContent);
        leftPanel.appendChild(snapsContent);

        // Switching Logic
        tabInv.onclick = () => {
            tabInv.classList.add("active");
            tabSnaps.classList.remove("active");
            invContent.style.display = "flex";
            snapsContent.style.display = "none";
        };
        tabSnaps.onclick = () => {
            tabSnaps.classList.add("active");
            tabInv.classList.remove("active");
            snapsContent.style.display = "flex";
            invContent.style.display = "none";
            loadSnapshots();
        };

        // --- Inventory Content ---
        const searchInput = makeEl("input", "moltbot-input");
        searchInput.placeholder = "Search nodes or models...";
        searchInput.style.marginBottom = "10px";

        const invList = makeEl("div", "moltbot-inventory-list");
        invList.style.flex = "1";
        invList.style.overflowY = "auto";
        invList.style.border = "1px solid var(--border-color, #444)";
        invList.style.padding = "0.5rem";

        invContent.appendChild(searchInput);
        invContent.appendChild(invList);

        // --- Snapshots Content ---
        const snapList = makeEl("div", "moltbot-snapshot-list");
        snapList.style.flex = "1";
        snapList.style.overflowY = "auto";
        snapList.style.border = "1px solid var(--border-color, #444)";
        snapList.style.padding = "0.5rem";

        snapsContent.appendChild(snapList);

        // --- Right Panel: Preflight Diagnostics ---
        const diagHeader = makeEl("h3", "", "Preflight Diagnostics");
        diagHeader.style.marginTop = "0";

        const diagDesc = makeEl("p", "", "Paste a workflow JSON (API format) to check for missing nodes/models compatible with this environment.");
        diagDesc.style.fontSize = "0.9em";
        diagDesc.style.opacity = "0.8";

        const jsonInput = makeEl("textarea", "moltbot-input");
        jsonInput.placeholder = 'Paste workflow JSON here... {"3": {"class_type": ...}}';
        jsonInput.style.flex = "1";
        jsonInput.style.fontFamily = "monospace";
        jsonInput.style.resize = "none";
        jsonInput.style.marginBottom = "10px";

        const actionsRow = makeEl("div");
        actionsRow.style.display = "flex";
        actionsRow.style.gap = "10px";

        const runBtn = makeEl("button", "moltbot-btn primary", "Run Preflight");
        const clearBtn = makeEl("button", "moltbot-btn", "Clear");

        actionsRow.appendChild(runBtn);
        actionsRow.appendChild(clearBtn);

        const resultsArea = makeEl("div", "moltbot-preflight-results");
        resultsArea.style.marginTop = "10px";
        resultsArea.style.padding = "10px";
        resultsArea.style.border = "1px solid var(--border-color, #444)";
        resultsArea.style.minHeight = "100px";
        resultsArea.style.display = "none";

        rightPanel.appendChild(diagHeader);
        rightPanel.appendChild(diagDesc);
        rightPanel.appendChild(jsonInput);
        rightPanel.appendChild(actionsRow);
        rightPanel.appendChild(resultsArea);

        // --- Logic ---

        let inventoryData = null;

        // Helper: Debounce
        function debounce(func, wait) {
            let timeout;
            return function (...args) {
                const context = this;
                clearTimeout(timeout);
                timeout = setTimeout(() => func.apply(context, args), wait);
            };
        }

        // Fetch Inventory
        async function loadInventory() {
            invList.innerHTML = "Loading...";
            const res = await moltbotApi.getInventory();
            if (res.ok) {
                inventoryData = res.data;
                renderInventoryList(res.data, searchInput.value);
            } else {
                invList.innerHTML = `<div class="error">Failed to load inventory: ${res.error}</div>`;
            }
        }

        async function loadSnapshots() {
            snapList.innerHTML = "Loading...";
            const res = await moltbotApi.listCheckpoints();
            if (res.ok) {
                renderSnapshots(res.data.checkpoints || []);
            } else {
                snapList.innerHTML = `<div class="error">Failed to load snapshots: ${res.error}</div>`;
            }
        }

        function renderInventoryList(data, query = "") {
            invList.innerHTML = "";
            const q = query.toLowerCase();
            const MAX_ITEMS_PER_CAT = 100;

            // Nodes
            const nodes = data.nodes || [];
            const filteredNodes = nodes.filter(n => n.toLowerCase().includes(q));

            if (filteredNodes.length > 0) {
                const h = makeEl("h4", "", `Nodes (${filteredNodes.length})`);
                h.style.margin = "5px 0";
                invList.appendChild(h);

                filteredNodes.slice(0, MAX_ITEMS_PER_CAT).forEach(n => {
                    const row = makeEl("div", "moltbot-inv-item", n);
                    row.style.fontSize = "0.9em";
                    row.style.padding = "2px 0";
                    invList.appendChild(row);
                });

                if (filteredNodes.length > MAX_ITEMS_PER_CAT) {
                    const more = makeEl("div", "", `...and ${filteredNodes.length - MAX_ITEMS_PER_CAT} more`);
                    more.style.fontStyle = "italic";
                    more.style.opacity = "0.6";
                    more.style.fontSize = "0.8em";
                    invList.appendChild(more);
                }
            }

            // Models
            const models = data.models || {};
            for (const [type, list] of Object.entries(models)) {
                const filteredVars = list.filter(m => m.toLowerCase().includes(q));
                if (filteredVars.length > 0) {
                    const h = makeEl("h4", "", `${type} (${filteredVars.length})`);
                    h.style.margin = "10px 0 5px 0";
                    invList.appendChild(h);

                    filteredVars.slice(0, MAX_ITEMS_PER_CAT).forEach(m => {
                        const row = makeEl("div", "moltbot-inv-item", m);
                        row.style.fontSize = "0.9em";
                        row.style.padding = "2px 0";
                        row.title = m; // tooltip
                        invList.appendChild(row);
                    });

                    if (filteredVars.length > MAX_ITEMS_PER_CAT) {
                        const more = makeEl("div", "", `...and ${filteredVars.length - MAX_ITEMS_PER_CAT} more`);
                        more.style.fontStyle = "italic";
                        more.style.opacity = "0.6";
                        more.style.fontSize = "0.8em";
                        invList.appendChild(more);
                    }
                }
            }
        }

        // Debounced Input
        searchInput.addEventListener("input", debounce(() => {
            if (inventoryData) renderInventoryList(inventoryData, searchInput.value);
        }, 300));

        // Preflight Action
        runBtn.onclick = async () => {
            const jsonStr = jsonInput.value.trim();
            if (!jsonStr) return;

            resultsArea.style.display = "block";
            resultsArea.innerHTML = "Running diagnostics...";

            let workflow;
            try {
                workflow = JSON.parse(jsonStr);
                if (workflow.prompt) workflow = workflow.prompt;
                else if (workflow.workflow) workflow = workflow.workflow;
            } catch (e) {
                resultsArea.innerHTML = `<div class="error">Invalid JSON: ${e.message}</div>`;
                return;
            }

            const res = await moltbotApi.runPreflight(workflow);
            if (res.ok) {
                renderResults(res.data, workflow);
            } else {
                resultsArea.innerHTML = `<div class="error">Error: ${res.error}</div>`;
            }
        };

        clearBtn.onclick = () => {
            jsonInput.value = "";
            resultsArea.style.display = "none";
            resultsArea.innerHTML = "";
        };

        function renderResults(report, workflow) {
            resultsArea.innerHTML = "";

            const headerRow = makeEl("div");
            headerRow.style.display = "flex";
            headerRow.style.justifyContent = "space-between";
            headerRow.style.alignItems = "center";

            const statusColor = report.ok ? "var(--success-color, #4caf50)" : "var(--error-color, #f44336)";
            const summaryEl = makeEl("div", "", "");
            summaryEl.style.color = statusColor;
            summaryEl.style.fontWeight = "bold";
            summaryEl.textContent = report.ok ? "✅ Workflow Compatible" : "❌ Issues Detected";

            const saveBtn = makeEl("button", "moltbot-btn", "Save Snapshot");
            saveBtn.onclick = async () => {
                const name = prompt("Snapshot Name:", "New Snapshot");
                if (name) {
                    const res = await moltbotApi.createCheckpoint(name, workflow);
                    if (res.ok) {
                        showToast("Snapshot saved");
                        if (tabSnaps.classList.contains("active")) loadSnapshots();
                    } else {
                        showToast("Save failed: " + res.error, "error");
                    }
                }
            };

            headerRow.appendChild(summaryEl);
            headerRow.appendChild(saveBtn);
            resultsArea.appendChild(headerRow);

            if (report.summary.missing_nodes > 0) {
                const section = makeEl("div");
                section.innerHTML = `<h4>Missing Nodes (${report.summary.missing_nodes})</h4>`;
                const ul = makeEl("ul");
                report.missing_nodes.forEach(m => {
                    const li = makeEl("li", "", `${m.class_type} (x${m.count})`);
                    ul.appendChild(li);
                });
                section.appendChild(ul);
                resultsArea.appendChild(section);
            }

            if (report.summary.missing_models > 0) {
                const section = makeEl("div");
                section.innerHTML = `<h4>Missing Models (${report.summary.missing_models})</h4>`;
                const ul = makeEl("ul");
                report.missing_models.forEach(m => {
                    const li = makeEl("li", "", `${m.type}: ${m.name} (x${m.count})`);
                    ul.appendChild(li);
                });
                section.appendChild(ul);
                resultsArea.appendChild(section);
            }
        }

        // Initial Load
        loadInventory();
    }
};

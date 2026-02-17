// CRITICAL: this tab module is loaded under /extensions/<pack>/web/tabs/*.js.
// Must resolve ComfyUI core app from /scripts/app.js via ../../../ prefix.
import { app } from "../../../scripts/app.js";
import { moltbotApi } from "../openclaw_api.js";
import { moltbotUI } from "../openclaw_ui.js";

/**
 * F52: Parameter Lab Tab
 * Allows users to configure and run bounded parameter sweeps.
 * F50: Includes "Compare Models" wizard.
 */
export const ParameterLabTab = {
    id: "parameter-lab",
    icon: "\uD83E\uDDEA", // Test Tube
    title: "Parameter Lab",
    tooltip: "Run experiments with parameter sweeps",

    // State
    dimensions: [],
    plan: null,
    experimentId: null,
    isRunning: false,
    results: [],

    render(container) {
        container.innerHTML = "";
        container.className = "moltbot-tab-content moltbot-lab-container";

        // 1. Header / Toolbar
        const header = document.createElement("div");
        header.className = "moltbot-lab-header";
        header.innerHTML = `
            <div class="moltbot-lab-title-wrap">
                <h3>Parameter Lab</h3>
                <p>Build bounded sweeps and compare model variants directly from canvas.</p>
            </div>
            <div class="moltbot-lab-actions">
                <button id="lab-history" class="moltbot-btn has-icon moltbot-lab-action-btn" title="View History">
                    <span class="moltbot-lab-action-icon">\uD83D\uDCDC</span>
                    <span class="moltbot-lab-action-label">History</span>
                </button>
                <div class="moltbot-separator"></div>
                <button id="lab-compare-models" class="moltbot-btn has-icon moltbot-lab-action-btn" title="Wizard: Compare Models">
                    <span class="moltbot-lab-action-icon">\u2696\uFE0F</span>
                    <span class="moltbot-lab-action-label">Compare Models</span>
                </button>
                <div class="moltbot-separator"></div>
                <button id="lab-add-dim" class="moltbot-btn moltbot-lab-action-btn">
                    <span class="moltbot-lab-action-icon">&#x2795;</span>
                    <span class="moltbot-lab-action-label">+ Dimension</span>
                </button>
                <button id="lab-generate" class="moltbot-btn moltbot-lab-action-btn">
                    <span class="moltbot-lab-action-icon">&#x1F9ED;</span>
                    <span class="moltbot-lab-action-label">Generate Plan</span>
                </button>
            </div>
        `;
        container.appendChild(header);
        this.container = container;

        const main = document.createElement("div");
        main.className = "moltbot-lab-main";
        container.appendChild(main);

        // 2. Configuration Area (Dimensions)
        const configCard = document.createElement("section");
        configCard.className = "moltbot-lab-card";
        configCard.innerHTML = `
            <div class="moltbot-lab-card-head">
                <h4>Dimensions</h4>
                <span class="moltbot-lab-meta" id="lab-dimension-count">0 configured</span>
            </div>
        `;
        const configArea = document.createElement("div");
        configArea.className = "moltbot-lab-config";
        configCard.appendChild(configArea);
        main.appendChild(configCard);
        this.configContainer = configArea;
        this.dimensionCountEl = configCard.querySelector("#lab-dimension-count");

        // 3. Plan / Results Area
        const resultsCard = document.createElement("section");
        resultsCard.className = "moltbot-lab-card";
        resultsCard.innerHTML = `
            <div class="moltbot-lab-card-head">
                <h4>Plan & Results</h4>
                <span class="moltbot-lab-meta">Live status</span>
            </div>
        `;
        const resultsArea = document.createElement("div");
        resultsArea.className = "moltbot-lab-results";
        resultsCard.appendChild(resultsArea);
        main.appendChild(resultsCard);
        this.resultsContainer = resultsArea;

        // Bind Events
        container.querySelector("#lab-add-dim").onclick = () => {
            this.setActiveToolbarButton("lab-add-dim");
            this.addDimensionUI();
        };
        container.querySelector("#lab-generate").onclick = () => {
            this.setActiveToolbarButton("lab-generate");
            this.generatePlan();
        };
        container.querySelector("#lab-compare-models").onclick = () => {
            this.setActiveToolbarButton("lab-compare-models");
            this.showCompareWizard();
        };
        container.querySelector("#lab-history").onclick = () => {
            this.setActiveToolbarButton("lab-history");
            this.showHistory();
        };

        // Start without forced selection state.
        this.setActiveToolbarButton(null);

        // Initial Render
        this.renderDimensions();

        // F50: Listen for Compare Request (once)
        if (!this._listeningForCompare) {
            window.addEventListener("moltbot:lab:compare", (e) => {
                const node = e.detail.node;
                if (node) {
                    this.showCompareWizard(node);
                }
            });
            this._listeningForCompare = true;
        }
    },

    async showHistory() {
        this.resultsContainer.innerHTML = "<div class='moltbot-loading'>Loading history...</div>";
        try {
            const res = await moltbotApi.fetch(moltbotApi._path("/lab/experiments"));
            if (res.ok && res.data) {
                this.renderHistoryList(res.data.experiments);
            } else {
                this.resultsContainer.innerHTML = "<div class='moltbot-error'>Failed to load history.</div>";
            }
        } catch (e) {
            this.resultsContainer.innerHTML = "<div class='moltbot-error'>Error: " + e.message + "</div>";
        }
    },

    setActiveToolbarButton(buttonId) {
        if (!this.container) return;
        this.container.querySelectorAll(".moltbot-lab-action-btn").forEach((btn) => {
            btn.classList.toggle("active", buttonId ? btn.id === buttonId : false);
        });
    },

    renderHistoryList(experiments) {
        this.resultsContainer.innerHTML = "";
        const header = document.createElement("div");
        header.className = "moltbot-lab-plan-header";
        header.innerHTML = `<h4>Experiment History</h4><span>${experiments.length} Records</span>`;
        this.resultsContainer.appendChild(header);

        const list = document.createElement("div");
        list.className = "moltbot-lab-run-list";

        if (experiments.length === 0) {
            list.innerHTML = "<div class='moltbot-hint'>No history found. Run a sweep or compare to see results here.</div>";
        }

        experiments.forEach(exp => {
            const item = document.createElement("div");
            item.className = "moltbot-lab-run-item";
            const dateStr = new Date(exp.created_at * 1000).toLocaleString();
            item.innerHTML = `
                <span class="run-idx">${exp.id.slice(0, 8)}</span>
                <span class="run-params">${dateStr}</span>
                <span class="run-status">${exp.completed_count}/${exp.run_count} runs</span>
                <button class="moltbot-btn-icon load-exp" title="Load Details">\u2192</button>
             `;
            item.querySelector(".load-exp").onclick = () => this.loadExperiment(exp.id);
            list.appendChild(item);
        });
        this.resultsContainer.appendChild(list);
    },

    async loadExperiment(expId) {
        this.resultsContainer.innerHTML = "<div class='moltbot-loading'>Loading details...</div>";
        try {
            const res = await moltbotApi.fetch(moltbotApi._path(`/lab/experiments/${expId}`));
            if (res.ok && res.data) {
                this.plan = res.data.experiment;
                this.experimentId = this.plan.experiment_id;
                this.renderPlan();
            }
        } catch (e) {
            this.resultsContainer.innerHTML = "<div class='moltbot-error'>Failed to load experiment.</div>";
        }
    },

    addDimensionUI(defaults = null) {
        // Add a default blank dimension or use defaults
        this.dimensions.push(defaults || {
            node_id: null,
            widget_name: "",
            values_str: "", // user input as CSV
            strategy: "grid"
        });
        this.renderDimensions();
    },

    removeDimension(index) {
        this.dimensions.splice(index, 1);
        this.renderDimensions();
    },

    renderDimensions() {
        this.configContainer.innerHTML = "";
        if (this.dimensionCountEl) {
            this.dimensionCountEl.textContent = `${this.dimensions.length} configured`;
        }
        if (this.dimensions.length === 0) {
            this.configContainer.innerHTML = "<div class='moltbot-hint'>No dimensions configured. Add one to start, or use 'Compare Models'.</div>";
            return;
        }

        this.dimensions.forEach((dim, idx) => {
            const row = document.createElement("div");
            row.className = "moltbot-lab-dim-row";
            row.innerHTML = `
                <div class="moltbot-form-group">
                    <label>Node ID</label>
                    <input type="number" class="dim-node-id" value="${dim.node_id || ''}" placeholder="ID">
                </div>
                <div class="moltbot-form-group">
                    <label>Widget</label>
                    <input type="text" class="dim-widget" value="${dim.widget_name}" placeholder="Name">
                </div>
                <div class="moltbot-form-group wide">
                    <label>Values (comma sep)</label>
                    <input type="text" class="dim-values" value="${dim.values_str}" placeholder="1.0, 1.5, 2.0">
                </div>
                <button class="moltbot-btn-icon remove-dim" title="Remove Dimension" aria-label="Remove Dimension">x</button>
            `;

            // Bind inputs
            row.querySelector(".dim-node-id").onchange = (e) => dim.node_id = parseInt(e.target.value);
            row.querySelector(".dim-widget").onchange = (e) => dim.widget_name = e.target.value;
            row.querySelector(".dim-values").onchange = (e) => dim.values_str = e.target.value;
            row.querySelector(".remove-dim").onclick = () => this.removeDimension(idx);

            this.configContainer.appendChild(row);
        });
    },

    // F50: Compare Models Wizard
    showCompareWizard(targetNode = null) {
        // 1. Scan for loader nodes if no target provided
        let node = targetNode;
        if (!node) {
            const nodes = app.graph._nodes.filter(n => n.type === "CheckpointLoaderSimple" || n.type === "LORALoader" || n.type === "UNETLoader");
            if (nodes.length === 0) {
                moltbotUI.showBanner("warning", "No Checkpoint/LoRA loaders found in workflow.");
                return;
            }
            node = nodes[0];
        }

        // 2. Find acceptable widget
        const widget = (node.widgets || []).find(
            w =>
                w.name === "ckpt_name" ||
                w.name === "lora_name" ||
                w.name === "unet_name"
        );

        if (!widget) {
            moltbotUI.showBanner("error", "Could not find model widget on node " + node.id);
            return;
        }

        // Reset dimensions
        if (this.dimensions.length > 0) {
            if (!confirm("This will clear current dimensions. Continue?")) return;
        }
        this.dimensions = [];

        // Add dimension pre-filled
        const options = widget.options?.values || [];
        let defaultValues = "";
        if (options.length > 0) {
            // Pick top 2 as example
            defaultValues = options.slice(0, 2).join(", ");
        }

        this.addDimensionUI({
            node_id: node.id,
            widget_name: widget.name,
            values_str: defaultValues,
            strategy: "compare"
        });

        moltbotUI.showBanner("info", `Setup comparison for Node ${node.id} (${node.title}). Edit values to select models.`);
    },

    async generatePlan() {
        // Validate
        const validDims = this.dimensions.filter(d => d.node_id && d.widget_name && d.values_str);
        if (validDims.length === 0) {
            moltbotUI.showBanner("error", "Please configure at least one valid dimension.");
            return;
        }

        // Prepare Payload
        const params = validDims.map(d => {
            // Parse values (try number, boolean, string)
            // For string values with commas (e.g. model names?), we need better CSV parsing.
            // But usually model names don't have commas.
            const rawVals = d.values_str.split(",").map(s => s.trim());
            const values = rawVals.map(v => {
                if (v === "true") return true;
                if (v === "false") return false;
                // Check if it looks like a number
                const n = parseFloat(v);
                // If it parses as a number but was meant as a string (e.g. "1.5" model name),
                // we might have issues. But usually models have extensions.
                // If it contains non-numeric chars, it's a string.
                if (!isNaN(n) && isFinite(n) && !v.match(/[a-zA-Z]/)) return n;
                return v;
            });

            return {
                node_id: d.node_id,
                widget_name: d.widget_name,
                values: values,
                strategy: d.strategy || "grid"
            };
        });

        const hasCompare = params.some(p => p.strategy === "compare");
        if (hasCompare && params.length !== 1) {
            moltbotUI.showBanner(
                "error",
                "Compare mode supports exactly one comparison dimension."
            );
            return;
        }

        try {
            // Serialize current workflow
            // Use app.graph.serialize() to get state
            const graphJson = JSON.stringify(app.graph.serialize());

            let res;
            if (hasCompare) {
                const compare = params[0];
                moltbotUI.showBanner("info", "Generating compare plan...");
                res = await moltbotApi.fetch(moltbotApi._path("/lab/compare"), {
                    method: "POST",
                    body: JSON.stringify({
                        workflow_json: graphJson,
                        items: compare.values,
                        node_id: compare.node_id,
                        widget_name: compare.widget_name
                    })
                });
            } else {
                moltbotUI.showBanner("info", "Generating sweep plan...");
                res = await moltbotApi.fetch(moltbotApi._path("/lab/sweep"), {
                    method: "POST",
                    body: JSON.stringify({
                        workflow_json: graphJson,
                        params: params
                    })
                });
            }

            if (res.ok && res.data) {
                this.plan = res.data.plan;
                this.experimentId = this.plan.experiment_id;
                this.renderPlan();
                moltbotUI.showBanner("success", `Plan generated: ${this.plan.runs.length} runs.`);
            } else {
                moltbotUI.showBanner("error", "Failed to generate plan: " + (res.error || "Unknown"));
            }
        } catch (e) {
            moltbotUI.showBanner("error", "Plan generation error: " + e.message);
        }
    },

    renderPlan() {
        this.resultsContainer.innerHTML = "";
        if (!this.plan) return;

        const header = document.createElement("div");
        header.className = "moltbot-lab-plan-header";
        header.innerHTML = `
            <h4>Experiment: ${this.experimentId.slice(0, 8)}</h4>
            <span>${this.plan.runs.length} Runs</span>
            <button id="lab-run-all" class="moltbot-btn primary">Run Experiment</button>
        `;
        this.resultsContainer.appendChild(header);

        const list = document.createElement("div");
        list.className = "moltbot-lab-run-list";

        this.plan.runs.forEach((run, idx) => {
            const item = document.createElement("div");
            item.className = "moltbot-lab-run-item";
            item.innerHTML = `
                <span class="run-idx">#${idx + 1}</span>
                <span class="run-params">${JSON.stringify(run).slice(0, 50)}...</span>
                <span class="run-status ${run.status || 'pending'}">${run.status || 'Pending'}</span>
                <button class="moltbot-btn-icon replay-run" title="Replay (Apply Values)">\u21A9\uFE0F</button>
            `;
            item.dataset.idx = idx;
            item.querySelector(".replay-run").onclick = (e) => {
                e.stopPropagation();
                this.replayRun(run);
            };
            list.appendChild(item);
        });

        this.resultsContainer.appendChild(list);

        // F50: Side-by-Side Comparison Layout
        if (this.plan.dimensions.some(d => d.strategy === "compare")) {
            this.resultsContainer.classList.add("moltbot-lab-compare-mode");
        } else {
            this.resultsContainer.classList.remove("moltbot-lab-compare-mode");
        }

        this.resultsContainer.querySelector("#lab-run-all").onclick = () => this.runExperiment();
    },

    async runExperiment() {
        if (this.isRunning) return;
        this.isRunning = true;
        moltbotUI.showBanner("info", "Starting experiment...");

        const items = this.resultsContainer.querySelectorAll(".moltbot-lab-run-item");

        // Subscribe to events for status updates
        const es = moltbotApi.subscribeEvents((data) => {
            if (!this.isRunning) return; // Note: we might want to keep listening even after queuing finishes
            const pid = data.prompt_id;
            if (!pid) return;

            // Find run with this prompt_id
            const runIdx = this.plan.runs.findIndex(r => r.prompt_id === pid);
            if (runIdx !== -1) {
                const item = items[runIdx];
                const statusSpan = item.querySelector(".run-status");

                if (data.event_type === "execution_success" || data.event_type === "completed") {
                    statusSpan.className = "run-status success";
                    statusSpan.textContent = "Completed";
                    // Update backend
                    moltbotApi.fetch(moltbotApi._path(`/lab/experiments/${this.experimentId}/runs/${runIdx}`), {
                        method: "POST", body: JSON.stringify({ status: "completed" })
                    });
                } else if (data.event_type === "execution_error" || data.event_type === "failed") {
                    statusSpan.className = "run-status error";
                    statusSpan.textContent = "Failed";
                    moltbotApi.fetch(moltbotApi._path(`/lab/experiments/${this.experimentId}/runs/${runIdx}`), {
                        method: "POST", body: JSON.stringify({ status: "failed" })
                    });
                } else if (data.event_type === "executing") {
                    statusSpan.className = "run-status running";
                    statusSpan.textContent = "Executing Node " + data.node;
                }
            }
        });

        this.es = es;

        try {
            for (let i = 0; i < this.plan.runs.length; i++) {
                // If user stops? (TODO: Add stop button)

                const run = this.plan.runs[i];
                const item = items[i];
                const statusSpan = item.querySelector(".run-status");

                statusSpan.className = "run-status running";
                statusSpan.textContent = "Queuing...";

                try {
                    // 1. Apply overrides
                    this.applyOverrides(run);

                    // 2. Queue Prompt & Capture ID
                    const res = await app.queuePrompt(0, 1);

                    if (res && res.prompt_id) {
                        run.prompt_id = res.prompt_id;
                        statusSpan.textContent = "Queued (" + res.prompt_id.slice(0, 4) + ")";

                        // Register with backend
                        moltbotApi.fetch(moltbotApi._path(`/lab/experiments/${this.experimentId}/runs/${i}`), {
                            method: "POST",
                            body: JSON.stringify({ status: "queued", output: { prompt_id: res.prompt_id } })
                        });
                    } else {
                        throw new Error("No prompt_id returned");
                    }

                } catch (e) {
                    statusSpan.className = "run-status error";
                    statusSpan.textContent = "Queue Failed";
                    console.error(e);
                }

                await new Promise(r => setTimeout(r, 1000));
            }
        } finally {
            // Keep monitoring
            moltbotUI.showBanner("success", "All runs queued. Monitoring progress...");
        }
    },

    replayRun(run) {
        if (confirm("Apply these parameter values to the current workflow?")) {
            this.applyOverrides(run);
            moltbotUI.showBanner("success", "Values applied to nodes.");
        }
    },

    applyOverrides(run) {
        Object.entries(run).forEach(([key, value]) => {
            if (key === "prompt_id" || key === "status") return;
            const [nodeId, widgetName] = key.split(".");
            const node = app.graph.getNodeById(parseInt(nodeId));
            if (node) {
                const widget = node.widgets.find(w => w.name === widgetName);
                if (widget) {
                    widget.value = value;
                }
            }
        });
    }
};

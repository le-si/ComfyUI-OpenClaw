import { moltbotApi } from "../openclaw_api.js";
import { showError, clearError } from "../openclaw_utils.js";

export const PlannerTab = {
    id: "planner",
    title: "Planner",
    icon: "pi pi-pencil",

    render(container) {
        container.innerHTML = `
            <div class="moltbot-panel">
                <div class="moltbot-scroll-area">
                    <div class="moltbot-card">
                        <div class="moltbot-section-header">Generation Goal</div>

                        <div class="moltbot-error-box" style="display:none" id="planner-error"></div>

                        <div class="moltbot-grid-2">
                             <div class="moltbot-input-group">
                                <label class="moltbot-label">Profile</label>
                                <select id="planner-profile" class="moltbot-select">
                                    <option value="SDXL-v1">SDXL v1</option>
                                    <option value="Flux-Dev">Flux Dev</option>
                                </select>
                            </div>
                            <div class="moltbot-input-group">
                                <label class="moltbot-label">Style / Directives</label>
                                <input type="text" id="planner-style" class="moltbot-input" placeholder="e.g. Cyberpunk, 8k...">
                            </div>
                        </div>

                        <div class="moltbot-input-group">
                            <label class="moltbot-label">Requirements</label>
                            <textarea id="planner-reqs" class="moltbot-textarea moltbot-textarea-sm" placeholder="Describe the image..."></textarea>
                        </div>

                        <!-- R38-Lite: Loading state container -->
                        <div id="planner-loading" style="display:none; margin: 12px 0; padding: 12px; background: var(--input-background); border-radius: 6px;">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <div class="spinner-border" style="width: 20px; height: 20px; border: 2px solid; border-color: var(--primary-color) transparent transparent transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                                <div>
                                    <div id="planner-stage" style="font-weight: 600; margin-bottom: 4px;">Preparing request...</div>
                                    <div id="planner-elapsed" style="font-size: 0.9em; opacity: 0.7;">Elapsed: 0s</div>
                                </div>
                            </div>
                            <button id="planner-cancel-btn" class="moltbot-btn" style="margin-top: 8px; width: 100%; background: var(--input-background); border: 1px solid var(--border-color);">Cancel</button>
                        </div>

                        <button id="planner-run-btn" class="moltbot-btn moltbot-btn-primary">Plan Generation</button>
                    </div>

                    <div id="planner-results" style="display:none;" class="moltbot-split-v">
                        <div class="moltbot-card">
                            <div class="moltbot-section-header">Plan Output</div>
                            <div class="moltbot-input-group">
                                <label class="moltbot-label">Positive</label>
                                <textarea id="planner-out-pos" class="moltbot-textarea moltbot-textarea-md" readonly></textarea>
                            </div>
                            <div class="moltbot-input-group">
                                <label class="moltbot-label">Negative</label>
                                <textarea id="planner-out-neg" class="moltbot-textarea" rows="2" readonly></textarea>
                            </div>
                            <div class="moltbot-input-group">
                                <label class="moltbot-label">Params (JSON)</label>
                                <textarea id="planner-out-params" class="moltbot-textarea moltbot-textarea-md" readonly></textarea>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <style>
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        `;

        // R38-Lite: Abort controller for cancellation
        let abortController = null;
        let timerInterval = null;
        let startTime = 0;

        const updateStage = (stage) => {
            container.querySelector("#planner-stage").textContent = stage;
        };

        const startTimer = () => {
            startTime = Date.now();
            timerInterval = setInterval(() => {
                const elapsed = Math.floor((Date.now() - startTime) / 1000);
                container.querySelector("#planner-elapsed").textContent = `Elapsed: ${elapsed}s`;
            }, 500);
        };

        const stopTimer = () => {
            if (timerInterval) {
                clearInterval(timerInterval);
                timerInterval = null;
            }
        };

        const showLoading = (show) => {
            container.querySelector("#planner-loading").style.display = show ? "block" : "none";
            container.querySelector("#planner-run-btn").style.display = show ? "none" : "block";
        };

        container.querySelector("#planner-run-btn").onclick = async () => {
            const profile = container.querySelector("#planner-profile").value;
            const reqs = container.querySelector("#planner-reqs").value;
            const style = container.querySelector("#planner-style").value;

            const resDiv = container.querySelector("#planner-results");

            clearError(container);
            resDiv.style.display = "none";

            // R38-Lite: Create abort controller
            abortController = new AbortController();

            showLoading(true);
            updateStage("Preparing request...");
            startTimer();

            try {
                // Stage 1: Preparing
                await new Promise(resolve => setTimeout(resolve, 100)); // Brief delay to show stage
                updateStage("Sending request to backend...");

                // Stage 2: Sending
                await new Promise(resolve => setTimeout(resolve, 50));
                updateStage("Waiting for provider response...");

                const res = await moltbotApi.runPlanner(
                    {
                        profile,
                        requirements: reqs,
                        style_directives: style
                    },
                    abortController.signal  // Pass signal (note: runPlanner needs to support this)
                );

                stopTimer();

                if (res.ok) {
                    updateStage("Parsing and validating output...");
                    resDiv.style.display = "flex"; // Re-enable flex layout
                    container.querySelector("#planner-out-pos").value = res.data.positive || "";
                    container.querySelector("#planner-out-neg").value = res.data.negative || "";
                    container.querySelector("#planner-out-params").value = JSON.stringify(res.data.params || {}, null, 2);
                    showLoading(false);
                } else if (res.error === "timeout") {
                    showLoading(false);
                    showError(container, "Request timed out");
                } else if (res.error === "cancelled") {
                    // User cancelled
                    showLoading(false);
                    showError(container, "Request cancelled by user");
                } else {
                    showLoading(false);
                    showError(container, res.error || "Planning failed");
                }
            } catch (err) {
                stopTimer();
                showLoading(false);
                showError(container, err.message || "Unexpected error");
            }
        };

        // R38-Lite: Cancel button handler
        container.querySelector("#planner-cancel-btn").onclick = () => {
            if (abortController) {
                abortController.abort();
                stopTimer();
                showLoading(false);
            }
        };
    }
};

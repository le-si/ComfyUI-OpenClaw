import { openclawApi } from "../openclaw_api.js";
import { showError, clearError } from "../openclaw_utils.js";

export const RefinerTab = {
    id: "refiner",
    title: "Refiner",
    icon: "pi pi-sliders-h",

    render(container) {
        container.innerHTML = `
            <div class="moltbot-panel">
                <div class="moltbot-scroll-area">
                    <div class="moltbot-card">
                         <div class="moltbot-section-header">Source Context</div>

                        <div class="moltbot-error-box" style="display:none"></div>

                        <div class="moltbot-input-group">
                            <label class="moltbot-label">Source Image</label>
                            <div style="display:flex; gap:10px; align-items:center;">
                                <input type="file" id="refiner-img-upload" class="moltbot-input" accept="image/png, image/jpeg">
                                <img id="refiner-img-preview" style="height:40px; border-radius:4px; display:none; border:1px solid #444;">
                            </div>
                        </div>

                        <div class="moltbot-input-group">
                            <label class="moltbot-label">Original Positive</label>
                            <textarea id="refiner-orig-pos" class="moltbot-textarea"></textarea>
                        </div>

                        <div class="moltbot-input-group">
                            <label class="moltbot-label">Original Negative</label>
                            <textarea id="refiner-orig-neg" class="moltbot-textarea" rows="2"></textarea>
                        </div>
                    </div>

                    <div class="moltbot-card">
                        <div class="moltbot-section-header">Goal / Issue</div>
                        <div class="moltbot-input-group">
                            <textarea id="refiner-issue" class="moltbot-textarea moltbot-textarea-sm" placeholder="What's wrong? or What to change?"></textarea>
                        </div>

                        <!-- R38-Lite: Loading state container -->
                        <div id="refiner-loading" style="display:none; margin: 12px 0; padding: 12px; background: var(--input-background); border-radius: 6px;">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <div class="spinner-border" style="width: 20px; height: 20px; border: 2px solid; border-color: var(--primary-color) transparent transparent transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                                <div>
                                    <div id="refiner-stage" style="font-weight: 600; margin-bottom: 4px;">Preparing request...</div>
                                    <div id="refiner-elapsed" style="font-size: 0.9em; opacity: 0.7;">Elapsed: 0s</div>
                                </div>
                            </div>
                            <button id="refiner-cancel-btn" class="moltbot-btn" style="margin-top: 8px; width: 100%; background: var(--input-background); border: 1px solid var(--border-color);">Cancel</button>
                        </div>

                        <button id="refiner-run-btn" class="moltbot-btn moltbot-btn-primary">Refine Prompts</button>
                    </div>


                    <div id="refiner-results" style="display:none;" class="moltbot-split-v">
                        <div class="moltbot-card">
                            <div class="moltbot-section-header">Refinement</div>
                            <div class="moltbot-input-group">
                                <label class="moltbot-label">Rationale</label>
                                <div id="refiner-rationale" class="moltbot-markdown-box"></div>
                            </div>
                            <div class="moltbot-input-group">
                                <label class="moltbot-label">New Positive</label>
                                <textarea id="refiner-new-pos" class="moltbot-textarea moltbot-textarea-md"></textarea>
                            </div>
                             <div class="moltbot-input-group">
                                <label class="moltbot-label">New Negative</label>
                                <textarea id="refiner-new-neg" class="moltbot-textarea" rows="2"></textarea>
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

        // Image preview logic
        const imgInput = container.querySelector("#refiner-img-upload");
        const imgPreview = container.querySelector("#refiner-img-preview");
        let currentImgB64 = null;

        imgInput.onchange = async () => {
            const file = imgInput.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    currentImgB64 = e.target.result; // data:image/...
                    imgPreview.src = currentImgB64;
                    imgPreview.style.display = "block";
                };
                reader.readAsDataURL(file);
            }
        };

        // R38-Lite: Abort controller for cancellation
        let abortController = null;
        let timerInterval = null;
        let startTime = 0;

        const updateStage = (stage) => {
            container.querySelector("#refiner-stage").textContent = stage;
        };

        const startTimer = () => {
            startTime = Date.now();
            timerInterval = setInterval(() => {
                const elapsed = Math.floor((Date.now() - startTime) / 1000);
                container.querySelector("#refiner-elapsed").textContent = `Elapsed: ${elapsed}s`;
            }, 500);
        };

        const stopTimer = () => {
            if (timerInterval) {
                clearInterval(timerInterval);
                timerInterval = null;
            }
        };

        const showLoading = (show) => {
            container.querySelector("#refiner-loading").style.display = show ? "block" : "none";
            container.querySelector("#refiner-run-btn").style.display = show ? "none" : "block";
        };

        container.querySelector("#refiner-run-btn").onclick = async () => {
            clearError(container);
            const resDiv = container.querySelector("#refiner-results");
            resDiv.style.display = "none";

            // R38-Lite: Create abort controller
            abortController = new AbortController();

            showLoading(true);
            updateStage("Preparing request...");
            startTimer();

            try {
                // Stage 1: Preparing
                await new Promise(resolve => setTimeout(resolve, 100));
                updateStage("Sending request to backend...");

                // Stage 2: Sending
                await new Promise(resolve => setTimeout(resolve, 50));
                updateStage("Waiting for provider response...");

                const res = await openclawApi.runRefiner(
                    {
                        image_b64: currentImgB64,
                        orig_positive: container.querySelector("#refiner-orig-pos").value,
                        orig_negative: container.querySelector("#refiner-orig-neg").value,
                        issue: container.querySelector("#refiner-issue").value
                    },
                    abortController.signal
                );

                stopTimer();

                if (res.ok) {
                    updateStage("Parsing and validating output...");
                    container.querySelector("#refiner-new-pos").value = res.data.refined_positive || "";
                    container.querySelector("#refiner-new-neg").value = res.data.refined_negative || "";
                    container.querySelector("#refiner-rationale").textContent = res.data.rationale || "";
                    resDiv.style.display = "flex";
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
                    showError(container, res.error || "Refinement failed");
                }

            } catch (e) {
                stopTimer();
                showLoading(false);
                showError(container, `Refine Failed: ${e.message}`);
            }
        };

        // R38-Lite: Cancel button handler
        container.querySelector("#refiner-cancel-btn").onclick = () => {
            if (abortController) {
                abortController.abort();
                stopTimer();
                showLoading(false);
            }
        };
    }
};

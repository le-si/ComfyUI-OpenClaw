import { showError, clearError } from "../openclaw_utils.js";

export const VariantsTab = {
    id: "variants",
    title: "Variants",
    icon: "pi pi-copy",

    render(container) {
        container.innerHTML = `
            <div class="moltbot-panel">
                <div class="moltbot-scroll-area">
                    <div class="moltbot-card">
                        <div class="moltbot-section-header">Variants Configuration</div>

                        <div class="moltbot-error-box" style="display:none"></div>

                        <div class="moltbot-input-group">
                            <label class="moltbot-label">Base Parameters (JSON)</label>
                            <textarea id="var-base-params" class="moltbot-textarea moltbot-textarea-md">{"width": 1024, "height": 1024, "seed": 0}</textarea>
                        </div>

                        <div class="moltbot-grid-2">
                             <div class="moltbot-input-group">
                                <label class="moltbot-label">Strategy</label>
                                <select id="var-strategy" class="moltbot-select">
                                    <option value="seeds">Seed Sweep (Count)</option>
                                    <option value="cfg">CFG Scale (Range)</option>
                                </select>
                            </div>

                            <!-- Dynamic inputs based on strategy -->
                            <div id="var-opts-seeds" class="var-opts moltbot-input-group">
                                <label class="moltbot-label">Count</label>
                                <input type="number" id="var-seed-count" class="moltbot-input" value="4" min="1" max="100">
                            </div>
                        </div>

                        <button id="var-run-btn" class="moltbot-btn moltbot-btn-primary">Generate Variants JSON</button>
                    </div>

                    <div class="moltbot-card">
                         <div class="moltbot-section-header">Resulting List</div>
                        <div class="moltbot-input-group">
                            <label class="moltbot-label">Output (List of Params)</label>
                            <textarea id="var-output" class="moltbot-textarea moltbot-textarea-lg" readonly></textarea>
                        </div>
                    </div>
                </div>
            </div>
        `;

        container.querySelector("#var-run-btn").onclick = () => {
            clearError(container);
            try {
                const baseStr = container.querySelector("#var-base-params").value;
                if (!baseStr.trim()) throw new Error("Base parameters required");

                let base;
                try {
                    base = JSON.parse(baseStr);
                } catch (e) {
                    throw new Error("Base parameters must be valid JSON");
                }

                const count = parseInt(container.querySelector("#var-seed-count").value) || 4;
                const variants = [];

                // Simple logic for MVP (Seed Sweep only)
                for (let i = 0; i < count; i++) {
                    const v = { ...base };
                    v.seed = (base.seed || 0) + i;
                    variants.push(v);
                }

                container.querySelector("#var-output").value = JSON.stringify(variants, null, 2);
            } catch (e) {
                showError(container, e.message);
            }
        };
    }
};

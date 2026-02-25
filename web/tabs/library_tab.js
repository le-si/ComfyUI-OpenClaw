import { openclawApi } from "../openclaw_api.js";
import { tabManager } from "../openclaw_tabs.js";
import { showError, clearError, parseJsonOrThrow } from "../openclaw_utils.js";

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

export const LibraryTab = {
    id: "library",
    title: "Library",
    icon: "pi pi-book",

    render(container) {
        // --- 1. Static Layout ---
        container.innerHTML = `
            <div class="openclaw-panel openclaw-panel moltbot-panel">
                 <div class="openclaw-card openclaw-card moltbot-card" style="border-radius:0; border:none; border-bottom:1px solid var(--moltbot-color-border);">
                    <div class="openclaw-section-header openclaw-section-header moltbot-section-header">Asset Library</div>
                    <div class="openclaw-error-box openclaw-error-box moltbot-error-box" style="display:none"></div>
                    <div class="openclaw-input-group openclaw-input-group moltbot-input-group">
                        <input type="text" id="lib-search" class="openclaw-input openclaw-input moltbot-input" placeholder="Search...">
                    </div>
                    <div class="openclaw-toolbar openclaw-toolbar moltbot-toolbar" style="margin-top:8px; display:flex; gap:5px;" id="lib-filter-btns">
                        <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-primary openclaw-btn-primary moltbot-btn-primary" data-cat="all">All</button>
                        <button class="openclaw-btn openclaw-btn moltbot-btn" data-cat="prompt">Prompts</button>
                        <button class="openclaw-btn openclaw-btn moltbot-btn" data-cat="params">Params</button>
                        <button class="openclaw-btn openclaw-btn moltbot-btn" data-cat="packs">Packs</button>
                        <button class="openclaw-btn openclaw-btn moltbot-btn" id="lib-new-btn" style="margin-left: auto;">+ New</button>
                    </div>
                    <input type="file" id="lib-pack-upload" accept=".zip" style="display:none">
                </div>

                <div id="lib-list" class="openclaw-scroll-area openclaw-scroll-area moltbot-scroll-area" style="padding:0;">
                    <!-- Items -->
                    <div class="openclaw-empty-state openclaw-empty-state moltbot-empty-state">Loading...</div>
                </div>
            </div>

            <!-- Editor Modal (Presets) -->
            <div id="lib-editor-overlay" class="openclaw-modal-overlay openclaw-modal-overlay moltbot-modal-overlay" style="display:none;">
                <div id="lib-editor" class="openclaw-modal openclaw-modal moltbot-modal">
                    <div class="openclaw-modal-header openclaw-modal-header moltbot-modal-header">
                        <span id="lib-editor-title">Edit Preset</span>
                        <input type="hidden" id="lib-edit-id">
                    </div>
                    <div class="openclaw-modal-body openclaw-modal-body moltbot-modal-body">
                         <div class="openclaw-input-group openclaw-input-group moltbot-input-group">
                            <label class="openclaw-label openclaw-label moltbot-label">Name</label>
                            <input type="text" id="lib-edit-name" class="openclaw-input openclaw-input moltbot-input">
                        </div>
                        <br>
                        <div class="openclaw-input-group openclaw-input-group moltbot-input-group">
                            <label class="openclaw-label openclaw-label moltbot-label">Category</label>
                            <select id="lib-edit-cat" class="openclaw-select openclaw-select moltbot-select">
                                <option value="general">General</option>
                                <option value="prompt">Prompt</option>
                                <option value="params">Params</option>
                            </select>
                        </div>
                        <br>
                        <div class="openclaw-input-group openclaw-input-group moltbot-input-group">
                            <label class="openclaw-label openclaw-label moltbot-label">Content (JSON)</label>
                            <textarea id="lib-edit-params-json" class="openclaw-textarea openclaw-textarea moltbot-textarea openclaw-textarea-md openclaw-textarea-md moltbot-textarea-md"></textarea>
                        </div>
                    </div>
                    <div class="openclaw-modal-footer openclaw-modal-footer moltbot-modal-footer">
                        <button class="openclaw-btn openclaw-btn moltbot-btn" id="lib-editor-cancel">Cancel</button>
                        <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-primary openclaw-btn-primary moltbot-btn-primary" id="lib-editor-save">Save</button>
                    </div>
                </div>
            </div>
        `;

        // --- 2. State & References ---
        const ui = {
            list: container.querySelector("#lib-list"),
            search: container.querySelector("#lib-search"),
            filters: container.querySelector("#lib-filter-btns"),
            newBtn: container.querySelector("#lib-new-btn"),
            packUpload: container.querySelector("#lib-pack-upload"),
            modal: {
                overlay: container.querySelector("#lib-editor-overlay"),
                el: container.querySelector("#lib-editor"),
                title: container.querySelector("#lib-editor-title"),
                id: container.querySelector("#lib-edit-id"),
                name: container.querySelector("#lib-edit-name"),
                cat: container.querySelector("#lib-edit-cat"),
                content: container.querySelector("#lib-edit-params-json"),
                save: container.querySelector("#lib-editor-save"),
                cancel: container.querySelector("#lib-editor-cancel"),
            }
        };

        let currentState = {
            category: null, // 'packs' is a special category here
            items: []
        };

        // --- 3. View Logic (Renderers) ---

        const renderPresetItem = (p) => `
            <div class="openclaw-list-item openclaw-list-item moltbot-list-item" style="padding: 10px; border-bottom: 1px solid var(--moltbot-color-border); display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <div style="font-weight: bold;">${escapeHtml(p.name)}</div>
                    <div style="font-size: var(--moltbot-font-sm); color: var(--moltbot-color-fg-muted); margin-top:4px;">
                        <span class="openclaw-badge openclaw-badge moltbot-badge" style="background:#555; color:#eee;">${escapeHtml(p.category)}</span>
                    </div>
                </div>
                <div style="display: flex; gap: 5px;">
                    ${getApplyButton(p)}
                    <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm" data-action="edit" data-id="${p.id}">Edit</button>
                    <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm openclaw-btn-danger openclaw-btn-danger moltbot-btn-danger" data-action="delete" data-id="${p.id}">Del</button>
                </div>
            </div>
        `;

        const renderPackItem = (p) => `
            <div class="openclaw-list-item openclaw-list-item moltbot-list-item" style="padding: 10px; border-bottom: 1px solid var(--moltbot-color-border); display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <div style="font-weight: bold;">${escapeHtml(p.name)} <span style="font-weight:normal; opacity:0.7">v${escapeHtml(p.version)}</span></div>
                    <div style="font-size: var(--moltbot-font-sm); color: var(--moltbot-color-fg-muted); margin-top:4px;">
                        <span class="openclaw-badge openclaw-badge moltbot-badge" style="background:#2c4f7c; color:#eee;">${escapeHtml(p.type)}</span>
                        <span style="margin-left:6px;">by ${escapeHtml(p.author)}</span>
                    </div>
                </div>
                <div style="display: flex; gap: 5px;">
                    <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm" data-action="export-pack" data-name="${p.name}" data-ver="${p.version}">Export</button>
                    <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm openclaw-btn-danger openclaw-btn-danger moltbot-btn-danger" data-action="delete-pack" data-name="${p.name}" data-ver="${p.version}">Uninst</button>
                </div>
            </div>
        `;

        function getApplyButton(p) {
            if (p.category === "prompt") {
                return `
                    <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm openclaw-btn-primary openclaw-btn-primary moltbot-btn-primary" data-action="apply" data-id="${p.id}">Plan</button>
                    <button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm openclaw-btn-primary openclaw-btn-primary moltbot-btn-primary" data-action="apply-refiner" data-id="${p.id}">Refine</button>
                `;
            } else if (p.category === "params") {
                return `<button class="openclaw-btn openclaw-btn moltbot-btn openclaw-btn-sm openclaw-btn-sm moltbot-btn-sm openclaw-btn-primary openclaw-btn-primary moltbot-btn-primary" data-action="apply" data-id="${p.id}">Use</button>`;
            }
            return "";
        }

        const renderList = () => {
            const term = ui.search.value.toLowerCase();
            const filtered = currentState.items.filter(item =>
                item.name.toLowerCase().includes(term)
            );

            if (filtered.length === 0) {
                ui.list.innerHTML = '<div class="openclaw-empty-state openclaw-empty-state moltbot-empty-state">No items found.</div>';
                return;
            }

            if (currentState.category === "packs") {
                ui.list.innerHTML = filtered.map(renderPackItem).join("");
            } else {
                ui.list.innerHTML = filtered.map(renderPresetItem).join("");
            }
        };

        // --- 4. Logic/Controllers ---

        const loadContent = async () => {
            clearError(container);
            ui.list.innerHTML = '<div style="padding: 20px; text-align: center;">Loading...</div>';

            let res;
            if (currentState.category === "packs") {
                res = await openclawApi.getPacks();
            } else {
                // If cat is 'all', allow backend/logic to handle null
                const cat = currentState.category === "all" ? null : currentState.category;
                res = await openclawApi.listPresets(cat);
            }

            if (res.ok) {
                currentState.items = res.data || (res.packs ? res.packs : []);
                renderList();
            } else {
                ui.list.innerHTML = '';
                showError(container, res.error);
            }
        };

        const openModal = (preset = null) => {
            ui.modal.overlay.style.display = "flex";
            if (preset) {
                ui.modal.title.textContent = "Edit Preset";
                ui.modal.id.value = preset.id;
                ui.modal.name.value = preset.name;
                ui.modal.cat.value = preset.category;
                ui.modal.content.value = JSON.stringify(preset.content, null, 2);
            } else {
                ui.modal.title.textContent = "New Preset";
                ui.modal.id.value = "";
                ui.modal.name.value = "New Preset";
                ui.modal.cat.value = "general";
                ui.modal.content.value = "{}";
            }
        };

        const closeModal = () => { ui.modal.overlay.style.display = "none"; };

        const savePreset = async () => {
            const id = ui.modal.id.value;
            const name = ui.modal.name.value.trim();
            const cat = ui.modal.cat.value;
            let content;

            try {
                content = parseJsonOrThrow(
                    ui.modal.content.value,
                    "Content must be valid JSON"
                );
            }
            catch (e) { alert(e.message); return; }

            if (!name) { alert("Name required"); return; }

            ui.modal.save.textContent = "Saving...";
            ui.modal.save.disabled = true;

            let res;
            if (id) res = await openclawApi.updatePreset(id, { name, category: cat, content });
            else res = await openclawApi.createPreset({ name, category: cat, content });

            ui.modal.save.textContent = "Save";
            ui.modal.save.disabled = false;

            if (res.ok) {
                closeModal();
                loadContent();
            } else {
                alert(`Save failed: ${res.error}`);
            }
        };

        const deleteItem = async (idOrName, version = null) => {
            if (!confirm("Are you sure you want to delete this item?")) return;

            let res;
            if (currentState.category === "packs") {
                res = await openclawApi.deletePack(idOrName, version);
            } else {
                res = await openclawApi.deletePreset(idOrName);
            }

            if (res.ok) loadContent();
            else alert(`Delete failed: ${res.error}`);
        };

        // Pack Import
        ui.packUpload.onchange = async () => {
            const file = ui.packUpload.files[0];
            if (!file) return;

            ui.list.innerHTML = '<div style="padding: 20px; text-align: center;">Importing Pack...</div>';

            const res = await openclawApi.importPack(file, false); // No overwrite by default for now
            if (res.ok) {
                alert(`Imported ${res.data.pack.name} v${res.data.pack.version}`);
                loadContent();
            } else {
                showError(container, res.error);
                // Reload to restore list
                setTimeout(loadContent, 2000);
            }
            ui.packUpload.value = ""; // Reset
        };

        const triggerNew = () => {
            if (currentState.category === "packs") {
                ui.packUpload.click();
            } else {
                openModal();
            }
        };

        // --- 5. Event Binding ---

        ui.search.addEventListener("input", renderList);

        ui.filters.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-cat]");
            if (!btn) return;

            ui.filters.querySelectorAll("button").forEach(b => b.classList.remove("openclaw-btn-primary", "openclaw-btn-primary", "moltbot-btn-primary"));
            btn.classList.add("openclaw-btn-primary", "openclaw-btn-primary", "moltbot-btn-primary");

            const cat = btn.dataset.cat;
            currentState.category = cat === "all" ? null : cat;

            // Update New Button Text
            ui.newBtn.textContent = currentState.category === "packs" ? "Import" : "+ New";

            loadContent();
        });

        ui.newBtn.addEventListener("click", triggerNew);

        // List Delegation
        ui.list.addEventListener("click", async (e) => {
            const btn = e.target.closest("button[data-action]");
            if (!btn) return;
            const action = btn.dataset.action;

            if (action === "edit") {
                const res = await openclawApi.getPreset(btn.dataset.id);
                if (res.ok) openModal(res.data);
                else showError(container, "Failed to load preset details");
            } else if (action === "delete") {
                await deleteItem(btn.dataset.id);
            } else if (action === "delete-pack") {
                await deleteItem(btn.dataset.name, btn.dataset.ver);
            } else if (action === "export-pack") {
                const res = await openclawApi.exportPack(btn.dataset.name, btn.dataset.ver);
                if (res.ok) {
                    // Create blob link and click it
                    const url = window.URL.createObjectURL(res.data);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `${btn.dataset.name}-${btn.dataset.ver}.zip`; // Or preserve filename from header if possible
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    setTimeout(() => window.URL.revokeObjectURL(url), 1000);
                } else {
                    showError(container, res.error);
                }
            } else if (action === "apply") {
                const res = await openclawApi.getPreset(btn.dataset.id);
                if (res.ok) applyPreset(res.data, "planner");
                else showError(container, "Failed to load preset for apply");
            } else if (action === "apply-refiner") {
                const res = await openclawApi.getPreset(btn.dataset.id);
                if (res.ok) applyPreset(res.data, "refiner");
                else showError(container, "Failed to load preset for apply");
            }
        });

        // Apply Logic reused...
        function applyPreset(preset, explicitTarget = null) {
            let targetTabId = explicitTarget;
            if (!targetTabId) {
                if (preset.category === "prompt") targetTabId = "planner";
                else if (preset.category === "params") targetTabId = "variants";
            }
            if (!targetTabId) return;

            tabManager.activateTab(targetTabId);
            setTimeout(() => {
                try {
                    const content = preset.content;
                    if (targetTabId === "planner") {
                        const pos = document.getElementById("planner-out-pos");
                        const neg = document.getElementById("planner-out-neg");
                        const resDiv = document.getElementById("planner-results");
                        if (resDiv) resDiv.style.display = "flex";
                        if (pos && content.positive) pos.value = content.positive;
                        if (neg && content.negative) neg.value = content.negative;
                    } else if (targetTabId === "variants") {
                        const baseParams = document.getElementById("var-base-params");
                        if (baseParams && content.params) baseParams.value = JSON.stringify(content.params, null, 2);
                    } else if (targetTabId === "refiner") {
                        const pos = document.getElementById("refiner-orig-pos");
                        const neg = document.getElementById("refiner-orig-neg");
                        if (pos && content.positive) pos.value = content.positive;
                        if (neg && content.negative) neg.value = content.negative;
                    }
                } catch (e) {
                    console.error("Failed to apply preset:", e);
                    alert("Error applying preset to target tab.");
                }
            }, 100);
        }

        ui.modal.cancel.addEventListener("click", closeModal);
        ui.modal.save.addEventListener("click", savePreset);
        ui.modal.overlay.addEventListener("click", (e) => {
            if (e.target === ui.modal.overlay) closeModal();
        });

        loadContent();
    }
};

/**
 * Widget builder logic for /register_service
 * Access mode (fabric_token / external), then for node-based services
 * one Input Form widget with instructions, per-field help text and output fields.
 */

let currentWidget = null;

// ── Access mode: fabric_token (app on the author's nodes) or external (link only) ──

function getAuthMode() {
    const checked = document.querySelector('input[name="auth-mode"]:checked');
    return checked ? checked.value : 'fabric_token';
}

function setAuthMode(mode) {
    const radio = document.getElementById(`mode-${mode}`);
    if (radio) radio.checked = true;
    applyAuthMode();
}

function applyAuthMode() {
    const external = getAuthMode() === 'external';
    // External services have no nodes, widget, code or project gate
    document.getElementById('external-url-section').classList.toggle('d-none', !external);
    for (const id of ['projects-section', 'nodes-section', 'widget-section', 'preview-section']) {
        document.getElementById(id).classList.toggle('d-none', external);
    }
    schedulePreview();
}

document.querySelectorAll('input[name="auth-mode"]').forEach(r => r.addEventListener('change', applyAuthMode));

// ── Live preview ──

let previewTimer = null;

function schedulePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 400);
}

async function updatePreview() {
    const codeEl = document.getElementById('preview-code');
    const statusEl = document.getElementById('preview-status');
    if (!codeEl) return;

    if (getAuthMode() === 'external') return;
    const collected = collectWidgets();
    const validWidgets = collected.filter(w => w.endpoint && w.label);

    if (validWidgets.length === 0) {
        codeEl.textContent = 'Configure the widget above to see the generated Flask code.';
        codeEl.classList.add('text-muted');
        statusEl.textContent = 'Idle';
        statusEl.className = 'badge bg-secondary me-2';
        return;
    }

    const nodes = collectNodes();
    const port = nodes.length > 0 ? nodes[0].port : 8080;

    statusEl.textContent = 'Updating...';
    statusEl.className = 'badge bg-info me-2';

    try {
        const resp = await fetch('/api/preview_template', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({widgets: validWidgets, port: port,
                                  auth_mode: getAuthMode(), access_project_ids: collectProjects()})
        });
        const data = await resp.json();
        if (resp.ok) {
            codeEl.textContent = data.template;
            codeEl.classList.remove('text-muted');
            statusEl.textContent = 'Up to date';
            statusEl.className = 'badge bg-success me-2';
        } else {
            statusEl.textContent = 'Error';
            statusEl.className = 'badge bg-danger me-2';
        }
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.className = 'badge bg-danger me-2';
    }
}

// ── Widget creation ──

function updatePaletteButtons() {
    // No-op: palette removed; widget is always present
}

function createWidgetCard(type) {
    removeWidget();

    // Always one Input Form; the endpoint defaults to the generated app's path
    type = 'form';
    const widgetId = 'w1';
    currentWidget = {
        id: widgetId,
        type: type,
        label: '',
        endpoint: '/api/service_config',
        method: 'GET',
        instructions: '',
        input_fields: [],
        output_fields: [],
        auto_refresh: false,
        refresh_interval_sec: 30
    };

    const card = document.createElement('div');
    card.className = 'card widget-config-card';
    card.dataset.widgetId = widgetId;

    card.innerHTML = `
        <div class="card-body">
            <div class="row g-3">
                <div class="col-md-5">
                    <label class="form-label">Label</label>
                    <input type="text" class="form-control widget-label" value="Service Config" placeholder="Widget label">
                </div>
                <div class="col-md-5">
                    <label class="form-label">Endpoint on your node</label>
                    <input type="text" class="form-control widget-endpoint" value="/api/service_config"
                           placeholder="/api/service_config">
                    <div class="form-text">Keep the default for the generated app, or enter your existing
                        API's path (e.g. <code>/v1/measure</code>). Your app must check the user's FABRIC
                        service token (<code>Authorization: Bearer</code>), as the generated app does.</div>
                </div>
                <div class="col-md-2">
                    <label class="form-label">Method</label>
                    <select class="form-select widget-method">
                        <option value="GET" selected>GET</option>
                        <option value="POST">POST</option>
                    </select>
                </div>
                <div class="col-12">
                    <label class="form-label">Description</label>
                    <textarea class="form-control widget-instructions" rows="2"
                              placeholder="Instructions shown to users above the form (optional)">An example Flask endpoint configuration</textarea>
                </div>
            </div>
            <hr class="my-3">
            <label class="form-label"><strong>Config Input Values</strong></label>
            <div id="input-fields-list"></div>
            <button type="button" class="btn btn-sm btn-outline-secondary mt-2" id="add-field-btn">
                + Add Input Field
            </button>
            <hr class="my-3">
            <label class="form-label"><strong>Config Return Values</strong></label>
            <div id="output-fields-list"></div>
            <button type="button" class="btn btn-sm btn-outline-secondary mt-2" id="add-output-btn">
                + Add Return Value
            </button>
            <div class="form-text mt-1">If no return values are added, the raw JSON response is shown.</div>
        </div>
    `;

    // Add input field handler
    const addFieldBtn = card.querySelector('#add-field-btn');
    if (addFieldBtn) {
        addFieldBtn.addEventListener('click', () => {
            addInputFieldRow(card.querySelector('#input-fields-list'));
        });
    }

    // Add output field handler
    card.querySelector('#add-output-btn').addEventListener('click', () => {
        addOutputFieldRow(card.querySelector('#output-fields-list'));
    });

    const canvas = document.getElementById('widget-canvas');
    canvas.appendChild(card);

    schedulePreview();
    return card;
}

function removeWidget() {
    const card = document.querySelector('.widget-config-card');
    if (card) card.remove();
    currentWidget = null;
}

// ── Input field rows ──

function addInputFieldRow(container, prefill) {
    const row = document.createElement('div');
    row.className = 'input-field-row mb-3 p-2 bg-light rounded';
    row.innerHTML = `
        <div class="d-flex gap-2 align-items-center mb-1">
            <input type="text" class="form-control form-control-sm field-name"
                   placeholder="Field name (used in code)" style="flex: 1;"
                   value="${prefill ? prefill.name : ''}">
            <input type="text" class="form-control form-control-sm field-label"
                   placeholder="Display label" style="flex: 1;"
                   value="${prefill ? (prefill.label || '') : ''}">
            <div class="form-check">
                <input type="checkbox" class="form-check-input field-required"
                       ${(!prefill || prefill.required !== false) ? 'checked' : ''}>
                <label class="form-check-label small">Req</label>
            </div>
            <button type="button" class="btn btn-sm btn-outline-danger remove-field-btn">&times;</button>
        </div>
        <input type="text" class="form-control form-control-sm field-help"
               placeholder="Help text shown below the field (optional)"
               value="${prefill ? (prefill.help_text || '') : ''}">
    `;
    row.querySelector('.remove-field-btn').addEventListener('click', () => { row.remove(); schedulePreview(); });
    container.appendChild(row);
    schedulePreview();
}

// ── Output field rows ──

function addOutputFieldRow(container, prefill) {
    const row = document.createElement('div');
    row.className = 'output-field-row mb-3 p-2 bg-light rounded';
    row.innerHTML = `
        <div class="d-flex gap-2 align-items-center mb-1">
            <input type="text" class="form-control form-control-sm ofield-key"
                   placeholder="Response JSON key" style="flex: 1;"
                   value="${prefill ? prefill.key : ''}">
            <input type="text" class="form-control form-control-sm ofield-label"
                   placeholder="Display label" style="flex: 1;"
                   value="${prefill ? (prefill.label || '') : ''}">
            <button type="button" class="btn btn-sm btn-outline-danger remove-ofield-btn">&times;</button>
        </div>
        <input type="text" class="form-control form-control-sm ofield-description"
               placeholder="Description shown above the output (optional)"
               value="${prefill ? (prefill.description || '') : ''}">
    `;
    row.querySelector('.remove-ofield-btn').addEventListener('click', () => { row.remove(); schedulePreview(); });
    container.appendChild(row);
    schedulePreview();
}

// ── Collect widget data ──

function collectWidgets() {
    if (!currentWidget) return [];
    const card = document.querySelector('.widget-config-card');
    if (!card) return [];

    currentWidget.label = card.querySelector('.widget-label').value.trim();
    currentWidget.endpoint = card.querySelector('.widget-endpoint').value.trim();
    currentWidget.method = card.querySelector('.widget-method').value;
    currentWidget.instructions = card.querySelector('.widget-instructions').value.trim();

    const arCheckbox = card.querySelector('.widget-auto-refresh');
    if (arCheckbox) {
        currentWidget.auto_refresh = arCheckbox.checked;
        const refreshInput = card.querySelector('.widget-refresh');
        if (refreshInput) currentWidget.refresh_interval_sec = parseInt(refreshInput.value) || 30;
    }

    // Input fields
    currentWidget.input_fields = [];
    card.querySelectorAll('.input-field-row').forEach(row => {
        const name = row.querySelector('.field-name').value.trim();
        if (name) {
            currentWidget.input_fields.push({
                name,
                label: row.querySelector('.field-label').value.trim() || name,
                type: 'text',
                required: row.querySelector('.field-required').checked,
                help_text: row.querySelector('.field-help').value.trim()
            });
        }
    });

    // Output fields
    currentWidget.output_fields = [];
    card.querySelectorAll('.output-field-row').forEach(row => {
        const key = row.querySelector('.ofield-key').value.trim();
        if (key) {
            currentWidget.output_fields.push({
                key,
                label: row.querySelector('.ofield-label').value.trim() || key,
                description: row.querySelector('.ofield-description').value.trim()
            });
        }
    });

    return [currentWidget];
}

// ── Project management ──

function addProjectRow(prefillUuid) {
    const container = document.getElementById('projects-list');
    const row = document.createElement('div');
    row.className = 'project-row d-flex gap-2 align-items-center mb-2';
    row.innerHTML = `
        <input type="text" class="form-control form-control-sm project-uuid"
               placeholder="FABRIC project UUID" value="${prefillUuid || ''}">
        <button type="button" class="btn btn-sm btn-outline-danger remove-project-btn">&times;</button>
    `;
    row.querySelector('.remove-project-btn').addEventListener('click', () => { row.remove(); schedulePreview(); });
    container.appendChild(row);
}

function collectProjects() {
    const rows = document.querySelectorAll('.project-row');
    const projects = [];
    for (const row of rows) {
        const uuid = row.querySelector('.project-uuid').value.trim();
        if (uuid) projects.push(uuid);
    }
    return projects;
}

document.getElementById('add-project-btn').addEventListener('click', () => addProjectRow());

// ── Node management ──

function addNodeRow(prefill) {
    const container = document.getElementById('nodes-list');
    const row = document.createElement('div');
    row.className = 'node-row d-flex gap-2 align-items-center mb-2';
    row.innerHTML = `
        <input type="text" class="form-control form-control-sm node-name"
               placeholder="Node Name" style="flex: 2;" value="${prefill ? prefill.name : ''}">
        <input type="text" class="form-control form-control-sm node-ipv6"
               placeholder="FABnetv6 IP Address" style="flex: 3;" value="${prefill ? prefill.ipv6 : ''}">
        <input type="number" class="form-control form-control-sm node-port"
               placeholder="Port" value="${prefill ? prefill.port : '8080'}" min="1" max="65535" style="max-width: 100px;">
        <button type="button" class="btn btn-sm btn-outline-danger remove-node-btn">&times;</button>
    `;
    row.querySelector('.remove-node-btn').addEventListener('click', () => row.remove());
    container.appendChild(row);
}

function collectNodes() {
    const rows = document.querySelectorAll('.node-row');
    const nodes = [];
    for (const row of rows) {
        const name = row.querySelector('.node-name').value.trim();
        const ipv6 = row.querySelector('.node-ipv6').value.trim();
        const port = parseInt(row.querySelector('.node-port').value);
        if (name && ipv6 && port) nodes.push({name, ipv6, port});
    }
    return nodes;
}

document.getElementById('add-node-btn').addEventListener('click', () => addNodeRow());

// ── Live preview triggers ──
document.getElementById('widget-canvas').addEventListener('input', schedulePreview);
document.getElementById('widget-canvas').addEventListener('change', schedulePreview);
document.getElementById('nodes-list').addEventListener('input', schedulePreview);
document.getElementById('projects-list').addEventListener('input', schedulePreview);

const copyBtn = document.getElementById('preview-copy-btn');
if (copyBtn) {
    copyBtn.addEventListener('click', () => {
        const code = document.getElementById('preview-code').textContent;
        navigator.clipboard.writeText(code).then(() => {
            copyBtn.textContent = 'Copied!';
            setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
        });
    });
}

// ── Edit mode ──

function loadServiceData(data) {
    document.getElementById('svc-name').value = data.name;
    document.getElementById('svc-name').readOnly = true;
    document.getElementById('svc-desc').value = data.description;
    document.getElementById('svc-color').value = data.icon_color;
    document.getElementById('svc-external-url').value = data.external_url || '';

    for (const uuid of data.access_project_ids) addProjectRow(uuid);
    for (const node of data.nodes) addNodeRow(node);
    if (data.access_project_ids.length === 0) addProjectRow();
    if (data.nodes.length === 0) addNodeRow();

    if (!data.widgets || data.widgets.length === 0) {
        createWidgetCard('form');  // external services have none; keep a default if mode is switched
    } else {
        const w = data.widgets[0];
        const card = createWidgetCard(w.type);
        card.querySelector('.widget-label').value = w.label || '';
        card.querySelector('.widget-endpoint').value = w.endpoint || '';
        card.querySelector('.widget-method').value = w.method || 'GET';
        card.querySelector('.widget-instructions').value = w.instructions || '';

        if (w.type === 'button' && w.auto_refresh) {
            const arCb = card.querySelector('.widget-auto-refresh');
            if (arCb) {
                arCb.checked = true;
                card.querySelector('.auto-refresh-interval').classList.remove('d-none');
                card.querySelector('.widget-refresh').value = w.refresh_interval_sec || 30;
            }
        }
        if (w.input_fields) {
            const fieldsList = card.querySelector('#input-fields-list');
            if (fieldsList) {
                for (const field of w.input_fields) addInputFieldRow(fieldsList, field);
            }
        }
        if (w.output_fields) {
            const outList = card.querySelector('#output-fields-list');
            for (const of_ of w.output_fields) addOutputFieldRow(outList, of_);
        }
    }
    setAuthMode(data.auth_mode || 'fabric_token');
}

// ── Initialize ──

if (window.EDIT_MODE && window.SERVICE_DATA) {
    loadServiceData(window.SERVICE_DATA);
} else {
    addProjectRow();
    addNodeRow();
    // Auto-create the widget form (Input Form, GET, /api/service_config)
    createWidgetCard('form');
}

// ── Register / Update ──

document.getElementById('register-btn').addEventListener('click', async () => {
    const errorDiv = document.getElementById('register-error');
    errorDiv.classList.add('d-none');

    const serviceName = document.getElementById('svc-name').value.trim().toLowerCase();
    const description = document.getElementById('svc-desc').value.trim();
    const nodes = collectNodes();
    const accessProjects = collectProjects();
    const iconColor = document.getElementById('svc-color').value;
    const authMode = getAuthMode();
    const externalUrl = document.getElementById('svc-external-url').value.trim();
    const showError = (msg) => { errorDiv.textContent = msg; errorDiv.classList.remove('d-none'); };

    if (!serviceName) { showError('Please fill in the service name.'); return; }

    let collectedWidgets = [];
    if (authMode === 'external') {
        if (!externalUrl) { showError('Please enter the service URL.'); return; }
    } else {
        if (accessProjects.length === 0) { showError('Please add at least one allowed project UUID.'); return; }
        if (nodes.length === 0) { showError('Please add at least one node.'); return; }
        collectedWidgets = collectWidgets();
        if (collectedWidgets.length === 0 || !collectedWidgets[0].label || !collectedWidgets[0].endpoint) {
            showError('Please add a widget with a label and endpoint.');
            return;
        }
        if (!collectedWidgets[0].endpoint.startsWith('/')) {
            showError('The endpoint must be a path starting with /, e.g. /api/service_config.');
            return;
        }
    }

    const isEdit = window.EDIT_MODE;
    const btn = document.getElementById('register-btn');
    btn.disabled = true;
    btn.textContent = isEdit ? 'Updating...' : 'Registering...';

    try {
        const resp = await fetch(isEdit ? '/api/update_service' : '/api/register_service', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                service_name: serviceName, description, nodes,
                access_project_ids: accessProjects, icon_color: iconColor,
                widgets: collectedWidgets, auth_mode: authMode, external_url: externalUrl
            })
        });
        const data = await resp.json();
        if (resp.ok) {
            window.location.href = isEdit ? '/my_services' : `/register_service/success/${serviceName}`;
        } else {
            errorDiv.textContent = data.error || 'Operation failed.';
            errorDiv.classList.remove('d-none');
        }
    } catch (e) {
        errorDiv.textContent = 'Network error.';
        errorDiv.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.textContent = isEdit ? 'Update Service' : 'Register Service';
    }
});

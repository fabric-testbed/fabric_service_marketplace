/**
 * Widget builder logic for /register_service
 * Access mode (fabric_token / link), then for node-based services
 * one Input Form widget with instructions, per-field help text and output fields.
 */

let currentWidget = null;

// ── Access mode: fabric_token (app on the author's nodes) or link (a website with its own login; admins only) ──

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
    const isLink = getAuthMode() === 'link';
    // Website links have no nodes, widget or code (they do have a service project)
    document.getElementById('link-url-section').classList.toggle('d-none', !isLink);
    for (const id of ['nodes-section', 'widget-section', 'preview-section']) {
        document.getElementById(id).classList.toggle('d-none', isLink);
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

    if (getAuthMode() === 'link') return;
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
                                  auth_mode: getAuthMode(), access_project_ids: previewProjects()})
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

// One service project per service, chosen by name from the service projects the
// user owns and saved by its ID.

const MY_SERVICE_PROJECTS = window.MY_SERVICE_PROJECTS || [];

function renderProjectChoices(selectedUuids) {
    const select = document.getElementById('svc-project');
    const current = (selectedUuids || [])[0] || '';
    savedProjects = selectedUuids || [];
    const addOption = (value, text) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        select.appendChild(option);
    };
    if (MY_SERVICE_PROJECTS.length === 0 && !current) {
        addOption('', 'You do not own a service project');
        select.disabled = true;
        return;
    }
    if (!current && MY_SERVICE_PROJECTS.length > 1) addOption('', 'Choose a service project…');
    for (const project of MY_SERVICE_PROJECTS) addOption(project.uuid, project.name);
    // The project a service already has stays selectable, even if it is not the user's own
    if (current && !MY_SERVICE_PROJECTS.some(p => p.uuid === current)) addOption(current, current);
    select.value = current || select.options[0].value;
    // A service from before the one-project rule: say what choosing another project does
    const others = (selectedUuids || []).length - 1;
    if (others > 0) {
        const note = document.getElementById('svc-project-note');
        note.textContent = `This service also allows ${others} other project${others > 1 ? 's' : ''}, ` +
            'from before services were limited to one. They are kept unless you choose a different ' +
            'project here; then only the chosen one is allowed.';
        note.classList.remove('d-none');
    }
}

function collectProjects() {
    const uuid = document.getElementById('svc-project').value;
    return uuid ? [uuid] : [];
}

// The projects the generated code should allow: what the service will have once
// saved. A service from before the one-project rule keeps all its projects while
// its first project is left as it is (as the portal does on save).
let savedProjects = [];

function previewProjects() {
    const chosen = collectProjects();
    if (savedProjects.length > 1 && chosen[0] === savedProjects[0]) return savedProjects;
    return chosen;
}

// ── Node management ──

function addNodeRow(prefill) {
    const container = document.getElementById('nodes-list');
    const row = document.createElement('div');
    row.className = 'node-row d-flex gap-2 align-items-center mb-2';
    row.innerHTML = `
        <input type="text" class="form-control form-control-sm node-name"
               placeholder="Node Name" style="flex: 2;" value="${prefill ? prefill.name : ''}">
        <input type="text" class="form-control form-control-sm node-ipv6"
               placeholder="FABnet address (v6 or v4), e.g. 2602:fcfb:… or 10.128.…" style="flex: 3;" value="${prefill ? prefill.ipv6 : ''}">
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
document.getElementById('svc-project').addEventListener('change', schedulePreview);

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
    document.getElementById('svc-title').value = data.title || data.name;
    document.getElementById('svc-name').value = data.name;
    document.getElementById('svc-name').readOnly = true;
    document.getElementById('svc-desc').value = data.description;
    document.getElementById('svc-color').value = data.icon_color;
    document.getElementById('svc-link-url').value = data.link_url || '';

    renderProjectChoices(data.access_project_ids);
    for (const node of data.nodes) addNodeRow(node);
    if (data.nodes.length === 0) addNodeRow();

    if (!data.widgets || data.widgets.length === 0) {
        createWidgetCard('form');  // website links have none; keep a default if mode is switched
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
    renderProjectChoices([]);
    addNodeRow();
    // Auto-create the widget form (Input Form, GET, /api/service_config)
    createWidgetCard('form');
}

// ── Field errors: shown on the field itself, which is scrolled into view ──

function clearFieldErrors() {
    document.querySelectorAll('.is-invalid').forEach(el => el.classList.remove('is-invalid'));
    document.querySelectorAll('.field-error').forEach(el => el.remove());
    document.getElementById('register-error').classList.add('d-none');
}

function fieldError(el, msg) {
    el.classList.add('is-invalid');
    const anchor = el.closest('.node-row, .input-group') || el;
    const note = document.createElement('div');
    note.className = 'field-error text-danger small mt-1';
    note.textContent = msg;
    anchor.after(note);
    el.scrollIntoView({behavior: 'smooth', block: 'center'});
    el.focus({preventScroll: true});
    // Once the user starts fixing it, the mark goes away
    const clear = () => { el.classList.remove('is-invalid'); note.remove(); };
    el.addEventListener('input', clear, {once: true});
    el.addEventListener('change', clear, {once: true});
}

// ── Service name → address ──
// The user types a name ("FABRIC LLM Service"); the address of the service's page
// ("fabric-llm-service") is filled in from it until they change the address themselves.

const SERVICE_NAME_RE = /^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$/;
let addressEdited = false;

function addressFromName(name) {
    return name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+/, '').slice(0, 50).replace(/-+$/, '');
}

document.getElementById('svc-title').addEventListener('input', (e) => {
    if (window.EDIT_MODE || addressEdited) return;
    document.getElementById('svc-name').value = addressFromName(e.target.value);
});
document.getElementById('svc-name').addEventListener('input', (e) => {
    addressEdited = e.target.value.trim() !== '';
});

// The first problem with the node rows as [field, message], or null
function nodeProblem() {
    const rows = [...document.querySelectorAll('.node-row')];
    if (rows.length === 0) return [document.getElementById('add-node-btn'), 'Please add at least one node.'];
    for (const row of rows) {
        const name = row.querySelector('.node-name');
        const address = row.querySelector('.node-ipv6');
        const port = row.querySelector('.node-port');
        const value = address.value.trim();
        if (!name.value.trim()) return [name, 'Give this node a name, e.g. the name it has in your slice.'];
        if (!value) return [address, "Enter the node's FABnet address (FABnetv6 or FABNetv4)."];
        const isV4 = /^\d{1,3}(\.\d{1,3}){3}$/.test(value);
        if (!isV4 && !value.includes(':')) {
            return [address, "That is not an IP address. Enter the node's FABnet address, " +
                             'e.g. 2602:fcfb:… (FABnetv6) or 10.128.… (FABNetv4).'];
        }
        const portNumber = parseInt(port.value);
        if (!(portNumber >= 1 && portNumber <= 65535)) return [port, 'Enter the port your app listens on (1 to 65535).'];
    }
    return null;
}

// ── Register / Update ──

document.getElementById('register-btn').addEventListener('click', async () => {
    const errorDiv = document.getElementById('register-error');
    clearFieldErrors();

    const titleInput = document.getElementById('svc-title');
    const nameInput = document.getElementById('svc-name');
    const title = titleInput.value.trim();
    const serviceName = nameInput.value.trim().toLowerCase();
    const description = document.getElementById('svc-desc').value.trim();
    const nodes = collectNodes();
    const accessProjects = collectProjects();
    const iconColor = document.getElementById('svc-color').value;
    const authMode = getAuthMode();
    const linkInput = document.getElementById('svc-link-url');
    const linkUrl = linkInput.value.trim();
    const showError = (msg) => { errorDiv.textContent = msg; errorDiv.classList.remove('d-none'); };

    if (!title) { fieldError(titleInput, 'Please give your service a name.'); return; }
    if (!SERVICE_NAME_RE.test(serviceName)) {
        fieldError(nameInput, 'Use lowercase letters, numbers and hyphens: 3 to 50 characters, ' +
                              'starting and ending with a letter or number. Example: fabric-llm-service');
        return;
    }

    if (accessProjects.length === 0) {
        fieldError(document.getElementById('svc-project'), 'Please choose the service project this service belongs to.');
        return;
    }

    let collectedWidgets = [];
    if (authMode === 'link') {
        if (!linkUrl) { fieldError(linkInput, "Please enter the service's web address."); return; }
    } else {
        const problem = nodeProblem();
        if (problem) { fieldError(problem[0], problem[1]); return; }
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
                service_name: serviceName, title, description, nodes,
                access_project_ids: accessProjects, icon_color: iconColor,
                widgets: collectedWidgets, auth_mode: authMode, link_url: linkUrl
            })
        });
        const data = await resp.json();
        if (resp.ok) {
            window.location.href = isEdit ? '/my_services' : `/register_service/success/${encodeURIComponent(serviceName)}`;
        } else if (data.field === 'service_name') {
            fieldError(nameInput, data.error);
        } else {
            showError(data.error || 'Operation failed.');
        }
    } catch (e) {
        showError('Could not reach the portal. Please check your connection and try again.');
    } finally {
        btn.disabled = false;
        btn.textContent = isEdit ? 'Update Service' : 'Register Service';
    }
});

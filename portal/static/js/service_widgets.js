/**
 * Widget interaction logic for /available_services/<service_name>
 * Supports structured output fields (if defined) or raw JSON fallback.
 */

const SERVICE_NAME = window.location.pathname.split('/available_services/')[1];
const OUTPUT_FIELDS = window.WIDGET_OUTPUT_FIELDS || [];

function getSelectedNode() {
    const sel = document.getElementById('node-select');
    return sel ? sel.value : '';
}

async function proxyRequest(widgetId, method, body) {
    const node = getSelectedNode();
    const qs = new URLSearchParams();
    if (node) qs.set('node', node);

    if (body && method === 'GET') {
        for (const [k, v] of Object.entries(body)) {
            if (v !== '' && v !== null && v !== undefined) qs.set(k, v);
        }
    }

    const url = `/available_services/${SERVICE_NAME}/proxy/${widgetId}?${qs.toString()}`;
    const options = { method, headers: {} };
    if (body && method === 'POST') {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    try {
        const resp = await fetch(url, options);
        return await resp.json();
    } catch (e) {
        return {error: `Request failed: ${e.message}`};
    }
}

function renderResponse(widgetId, data) {
    if (OUTPUT_FIELDS.length > 0) {
        // Distribute values to individual output field containers
        const outputArea = document.getElementById(`output-area-${widgetId}`);
        if (!outputArea) return;

        outputArea.querySelectorAll('.output-field-container').forEach(container => {
            const key = container.dataset.key;
            const valueEl = container.querySelector('.output-value');
            if (!valueEl) return;

            const value = data[key];
            if (value === undefined || value === null) {
                valueEl.textContent = '—';
            } else if (typeof value === 'object') {
                valueEl.textContent = JSON.stringify(value, null, 2);
            } else {
                valueEl.textContent = String(value);
            }
        });
    } else {
        // Fallback: raw JSON
        const container = document.getElementById(`response-${widgetId}`);
        if (!container) return;
        container.innerHTML = `<pre class="bg-light p-2 rounded small mb-0">${
            JSON.stringify(data, null, 2)
        }</pre>`;
    }
}

// Button widgets
document.querySelectorAll('.widget-action-btn').forEach(btn => {
    const widgetId = btn.dataset.widgetId;
    const method = btn.dataset.method || 'GET';
    const autoRefresh = btn.dataset.autoRefresh === 'true';
    const refreshInterval = parseInt(btn.dataset.refreshInterval) || 30;

    async function runAction() {
        btn.disabled = true;
        const origText = btn.textContent;
        btn.textContent = 'Loading...';
        const data = await proxyRequest(widgetId, method, null);
        renderResponse(widgetId, data);
        btn.disabled = false;
        btn.textContent = origText;
    }

    btn.addEventListener('click', runAction);
    if (autoRefresh) {
        runAction();
        setInterval(runAction, refreshInterval * 1000);
    }
});

// Form widgets
document.querySelectorAll('.widget-form').forEach(form => {
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const widgetId = form.dataset.widgetId;
        const method = form.dataset.method || 'POST';

        const formData = {};
        form.querySelectorAll('input[name]').forEach(input => {
            formData[input.name] = input.value;
        });

        const submitBtn = form.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Submitting...';

        const data = await proxyRequest(widgetId, method, formData);
        renderResponse(widgetId, data);

        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit';
    });
});

# FABRIC Service Portal — Design Document

## 1. Overview

The **FABRIC Service Portal** is a self-service web application for FABRIC testbed researchers. It lets authenticated authors register services, generates working Flask code automatically, and gives consumers a shareable web page per service. A service is one of two kinds (§6.1): an app on the author's FABRIC nodes, which receives each user's FABRIC service token and can check it itself (**node service**, `fabric_token`), or an existing website the portal links to (**website link**, `link`, added by admins). For node services the page's widget proxies to the author's backend over the private FABnetv6 network.

The system has three parts: the **portal** (central infrastructure, public IP), the **gateway** (a FABRIC VM with FABNetv4Ext + FABnetv6) and the authors' **service nodes**. The portal handles everything users see and relays widget calls through the gateway, which reaches service nodes over FABnetv6. It provides:

1. A **landing page** (no auth) with two entry points: register a service, or browse available services.
2. A **login** for authors and consumers: a "Sign in with FABRIC" button for the automated login (e.g. CILogon, to be added), plus a token test page for testing.
3. A **registration form** with an access-mode choice, a widget builder with live Flask code preview, and an edit mode for existing services.
4. An **available services gallery** listing all registered services.
5. **Individual service pages** where consumers in a service's allowed projects use its widget (node services), or follow its link (website links).
6. An **admin dashboard** for user/service management and audit logs.

A **SQLite database** stores persistent user identity, service ownership, and audit logs. Authentication is delegated to FABRIC UIS; the DB tracks *who* users are across token changes, *who owns what*, and *who used what*.

---

## 2. Infrastructure Layout

```
   Browser
      │ HTTPS
      ▼
 ┌──────────────────────┐   Central infrastructure (public IP)
 │ portal/              │   Nginx + Gunicorn + Flask + SQLite
 │ UI, login, registry, │   Decides who may use what. Logs usage.
 │ admin, audit logs    │
 └──────────┬───────────┘
            │ HTTPS → gateway FABNetv4Ext public IP
            │ X-Gateway-Secret, from portal IP only
            ▼
 ┌──────────────────────┐   FABRIC VM: FABNetv4Ext + FABnetv6
 │ gateway/             │   Nginx + Gunicorn + Flask, no database
 │ stateless relay      │   Forwards only to GATEWAY_ALLOWED_TARGETS
 └──────────┬───────────┘
            │ HTTP over FABnetv6 (2602:fcfb::/40)
            │ Authorization: Bearer <user's FABRIC service token>
      ┌─────┴─────┐
      ▼           ▼
  [node 1]    [node 2]       service authors' slices
```

**Why the split:** central infrastructure cannot reach FABnetv6, while a FABRIC VM can.
The VM part is kept as small as possible: it holds no user data, and if it is
compromised or its slice expires, only the relay is affected.

**Trust between portal and gateway:** the gateway accepts a request only if
(1) the caller's IP is in `GATEWAY_ALLOWED_SOURCES` and (2) it carries the shared
secret. The IP rule is enforced by both Nginx (generated from `gateway.env`, so
other callers never reach Python) and `gateway.py`. TLS is pinned to the gateway's self-signed cert (`GATEWAY_TLS_VERIFY`).
The gateway refuses any target outside `GATEWAY_ALLOWED_TARGETS`, so it cannot
be used as an open proxy.

The gateway VM's IPv4 policy routing and FABnetv6 route are **not persistent**
across reboots or slice reconfiguration (see SETUP.md).

---

## 3. Page Flow

```
                    ┌──────────────────────────────────┐
                    │         /  (Landing Page)        │
                    │         NO AUTH                  │
                    │                                  │
                    │  [Register a Service]            │
                    │  [Available Services]            │
                    └──────┬────────────────┬──────────┘
                           │                │
                           ▼                ▼
                    ┌──────────────┐  ┌──────────────────────┐
                    │  /login       │  │  /available_services  │
                    │  Sign in      │  │  Gallery (no auth)    │
                    │  Checks       │  │  Click card ────┐     │
                    │  PORTAL_PROJECT│ └─────────────────│─────┘
                    └──────┬────────┘                    │
                           │ OK                          ▼
                           ▼                 ┌──────────────────────────┐
                    ┌──────────────┐          │ /available_services/<svc> │
                    │ /register_   │          │ Log in if needed         │
                    │ service      │          │ Checks access_project    │
                    │ Widget builder│         │ After auth: widgets       │
                    │ Live preview │          │ (Input Form widget)      │
                    │ Examples     │          │ Target node dropdown     │
                    └──────┬───────┘          └──────────────────────────┘
                           │
                           ▼
                    ┌──────────────────────────────┐
                    │ Success page                  │
                    │ Shows URL + Flask template    │
                    └──────────────────────────────┘

       /my_services ── edit/delete your own services (AUTH REQUIRED)
       /edit_service/<name> ── same form, pre-filled
       /admin ── admin dashboard (admin role only)
```

---

## 4. Page Details

### 4.1 Landing Page (`/`)
- **No auth required.**
- Title: **FABRIC Service Portal**
- Two cards: *Register a FABRIC Service* → `/login`, *Available FABRIC Services* → `/available_services`.

### 4.2 Login Page (`/login`)
- **Sign in with FABRIC** button → `PORTAL_LOGIN_URL` (the automated login, e.g. CILogon); disabled while unset.
- "For testing" link to `/token_test`, where a FABRIC service token can be pasted to log in.
- Accepts either the full JSON blob from the FABRIC portal (containing `id_token`, `refresh_token`, etc.) or just the `id_token` string.
- `POST /auth/login` (used by `/token_test`) validates the token against FABRIC UIS (see §6).
- **Success** → session set. Members of `PORTAL_PROJECT_ID` go to `/register_service`; others go to `/available_services` (they can use services but not register them).

### 4.3 Widget Builder (`/register_service`)
Author required. Four areas:
1. **How do users get access?** (shown to admins only): App on my FABRIC nodes, or Website link (with a URL field). Other authors always register an app on their nodes. See §6.1.
2. **Service Details**: service name, description, icon color; for node services also allowed projects (multi) and slice nodes (multi).
3. **Widget** (node services): one Input Form widget, GET or POST, with configurable input and output fields. Its endpoint (the path on the node) defaults to `/api/service_config`, the generated app's path, and can be changed to an existing API's own path.
4. **Flask App Preview** (node services): live-updating preview of the generated Flask code, with Copy button.

Submits to `POST /api/register_service`.

### 4.4 Edit Mode (`/edit_service/<name>`)
Auth required + owner. Same page as the widget builder, but pre-filled with the existing service's data and submits to `POST /api/update_service`. Service name is read-only.

### 4.5 Registration Success Page (`/register_service/success/<service_name>`)
Shows the service URL. Node services: the generated Flask app (same as the live preview) and how to run it. Website links: "nothing to install".

### 4.6 Available Services Gallery (`/available_services`)
No auth required. Grid of service cards with icon, name, description, owner, status, and a "Website link" badge for website links. Click → service page.

### 4.7 Individual Service Page (`/available_services/<service_name>`)
- **Website link**: shows title/description and an "Open" link to the service's URL, for everyone. Nothing is proxied.
- **Node service, logged in and in an allowed project**: access is granted automatically; the widget is interactive. If the service has multiple nodes, a Target Node dropdown appears.
- **Node service, logged in but not in an allowed project**: explanation, no widget.
- **Node service, not logged in**: a Log in button (returns to the service page after login).

### 4.8 My Services Page (`/my_services`)
Auth required. Lists the logged-in user's services with node-name badges, status, and Delete buttons. Clicking a service name opens it in edit mode.

### 4.9 Admin Dashboard (`/admin`)
Admin role only. View all users, all services, and audit logs (auth + usage).

---

## 5. Database Schema

```sql
-- Users: one row per FABRIC identity, keyed by FABRIC UUID.
CREATE TABLE users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fabric_uuid     TEXT UNIQUE NOT NULL,
    username        TEXT NOT NULL,
    email           TEXT,
    role            TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at   TIMESTAMP
);

-- Services: one row per registered service.
CREATE TABLE services (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT UNIQUE NOT NULL,
    description             TEXT,
    owner_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    auth_mode               TEXT NOT NULL DEFAULT 'fabric_token',  -- 'fabric_token' | 'link'
    link_url                TEXT,           -- website links only
    access_project_id       TEXT,           -- legacy single project (first of the list)
    access_project_ids_json TEXT,           -- JSON array of allowed project UUIDs
    node_ipv6               TEXT,           -- legacy single node (first of the list)
    node_port               INTEGER,        -- legacy single port
    nodes_json              TEXT,           -- JSON array of {name, ipv6, port}
    icon_color              TEXT DEFAULT '#3B82F6',
    canvas_json             TEXT NOT NULL,  -- JSON array of widget configs
    status                  TEXT NOT NULL DEFAULT 'active',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Usage log: every proxy request (widget interaction).
CREATE TABLE usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id      INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id),
    consumer_name   TEXT,
    widget_id       TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    method          TEXT NOT NULL,
    request_params  TEXT,
    response_status INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Auth log: every login attempt (portal + per-service).
CREATE TABLE auth_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    fabric_uuid     TEXT,
    username        TEXT,
    auth_type       TEXT NOT NULL,   -- 'portal' | 'service'
    service_name    TEXT,
    success         BOOLEAN NOT NULL,
    error_message   TEXT,
    ip_address      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Logged-in users' FABRIC tokens, passed to service nodes on their behalf.
-- The session cookie holds only `ref`. Deleted at logout; expired with the session.
CREATE TABLE user_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    token           TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

For website links `nodes_json`, `node_*` and `access_project_*` are NULL and
`canvas_json` is `'[]'`. Existing databases are upgraded at start-up
(`_upgrade_database` in `app.py`): `auth_mode` / `link_url` are added, and
services from the removed API-key mode become `fabric_token`. An old `api_key`
column is left in place, unused.

**Key design decisions:**

| Decision | Rationale |
|---|---|
| `fabric_uuid` as stable identity | The same user may present different tokens over time; UUID from FABRIC UIS `/whoami` is permanent. |
| `nodes_json` alongside legacy `node_ipv6`/`node_port` | Each service can run on multiple nodes; the legacy columns store the first node for backward compatibility. |
| `access_project_ids_json` alongside legacy `access_project_id` | Service authors can authorize multiple projects for consumer access. |
| `canvas_json` TEXT column | Widget config is variable-shape; JSON in a TEXT column keeps the relational schema simple while structured fields (owner, name) stay queryable. |
| `user_tokens` table instead of the session cookie | FABRIC JWTs can exceed the 4 KB cookie limit, and tokens should not travel back and forth with every request. |
| `ondelete='CASCADE'` + SQLAlchemy `cascade='all, delete-orphan'` on Service→UsageLog | Deleting a service also deletes its usage logs (both at DB level and ORM level for SQLite compatibility). |

---

## 6. Authentication and Access

There is **one login** for everyone. Every login method only obtains the user's
**FABRIC token** and calls `login_with_fabric_token(token)` (`portal/routes/auth.py`).
That shared code (`fetch_fabric_identity`, against `FABRIC_UIS_URL`) turns it into an identity
`{uuid, name, email, project_ids, token}`, and access is decided from `project_ids`:

| Action | Rule |
|---|---|
| Register / edit services | member of `PORTAL_PROJECT_ID`, or admin |
| Use a node service | member of one of the service's `access_project_ids` |
| Register / edit a website link | admin only (the form hides the option from others; the server refuses it) |
| Use a website link | anyone (the service has its own login) |

Projects come from the user's FABRIC roles (`/people/<uuid>`), not from the
token's claims, so any valid token yields the full project list. `token` is the
user's FABRIC token, stored server-side in `user_tokens` and passed to service nodes.

Only tokens whose `scope` claim is `PORTAL_REQUIRED_TOKEN_SCOPE` (default
`service`) are accepted, at login, on `/token_test` and by the generated node
apps. A `resource` token can create and delete slices as the user, so it must
never be passed to a service author's node.

Login providers:

| Mode | Flow |
|---|---|
| token test (testing) | On `/token_test` the user pastes a FABRIC service token; `fetch_fabric_identity` validates it with FABRIC UIS (`/whoami`, `/people/{uuid}`) and extracts project UUIDs from role names (`<project-uuid>-pm` / `-po`). |
| automated login (to do) | Any flow (CILogon, FABRIC's existing login) that obtains the user's FABRIC service token and calls `login_with_fabric_token`; `PORTAL_LOGIN_URL` points the Sign in button at it. See SETUP.md section 4. |

A logged-in user who opens a service page is granted access automatically if
their projects match, so there is no second token paste.

### 6.1 Service kinds

Chosen per service at registration (`services.auth_mode`):

| | `fabric_token` (node service) | `link` (website link, admins only) |
|---|---|---|
| Portal checks project membership | yes | no |
| Node checks | optional, on by default (`CHECK_FABRIC_TOKEN`): `Authorization: Bearer` service token with FABRIC UIS + allowed projects | — |
| Node receives | `Authorization`, `X-Fabric-User-*` | not proxied |
| Callable without the portal | yes, from FABnetv6, with the user's own service token | yes (website) |
| Health check | `GET /health` on the node, no token | — |
| Registration needs | projects, nodes, widget | URL |
| Reference node app | `service_node/example_app.py` | — |

With the token check on, node services are checked twice on purpose: the portal
gates the widget, and the node checks independently because it is also reachable
directly over FABnetv6. The author may turn the node's check off; the node then
relies on the portal's check and the `X-Fabric-User-*` headers, and anyone on
FABnetv6 can call it directly.

An earlier API-key mode (the node trusted a per-service key that only the
gateway held) was removed once FABRIC service tokens made it safe to pass the
user's token to every node.

---

## 7. Widget Builder

### 7.1 Widget

Each service has **one Input Form widget** that calls one endpoint on the
service node. It defaults to `/api/service_config` (what the generated app and
the example serve); authors with an existing API enter its own path instead,
e.g. `/v1/measure`. Whatever serves it must check the user's FABRIC service
token, as the generated app does:

| Method | Inputs arrive as | Use for |
|---|---|---|
| GET | query parameters | reading / running something without side effects |
| POST | JSON body | changing state on the node |

Each input field has a name, display label, help text and required flag. All
values are strings. Optional output fields map keys in the JSON response to
labelled boxes on the service page; without them the raw JSON is shown.

### 7.2 Widget Config (stored in `services.canvas_json`)

```json
[
  {
    "id": "w1",
    "type": "form",
    "label": "Service Config",
    "endpoint": "/api/service_config",
    "method": "GET",
    "input_fields": [
      {"name": "target", "label": "Target", "type": "text", "required": true, "help_text": ""}
    ],
    "output_fields": [
      {"key": "result", "label": "Result", "description": ""}
    ]
  }
]
```

### 7.3 Reference example

`service_node/example_app.py` serves `/api/service_config` for GET (inputs as
query parameters) or POST (JSON body) and returns JSON. It refuses tokens that
aren't `service` scope, validates the `Authorization: Bearer` token with FABRIC
UIS (`FABRIC_UIS_URL`), requires one of `ALLOWED_PROJECTS`, caches results for
5 minutes, and reads the caller from the validated token.

The registration form hides projects, nodes, widget and preview for website links.

### 7.4 Live Preview

The widget builder includes a live Flask code preview that updates automatically (400ms debounce) as the author edits. It calls `POST /api/preview_template` with the current widget config, which runs the same `generate_flask_template` function used at registration time. This guarantees WYSIWYG.

---

## 8. Template Generator

`portal/template_generator.py` produces the same shape as the reference example,
tailored to the widget config:

- Four marked parts: setup (keep), `GET /health` returning `{"status": "ok"}` without a token (keep), the FABRIC token check (optional), and the endpoints (fill in, marked `>>> FILL IN <<<`).
- `require_auth`: with `CHECK_FABRIC_TOKEN = True` (default) it refuses non-service tokens, validates the token with UIS, and
  requires one of the service's allowed projects (filled in as `ALLOWED_PROJECTS`); with `False` it lets requests through.
- `/health` can't be used as a widget endpoint (registration refuses it).
- A `caller()` helper (verified user with the check on; the portal's `X-Fabric-User-*` headers, marked `verified: false`, with it off); one route per widget with the configured method (`@app.route('/api/service_config', methods=['GET'])`); each input field read from `request.args` (GET) or the JSON body (POST); the configured output fields returned as `"TODO"` placeholders, or `{"status": "ok", "caller": ...}` if none; `app.run(host='::', port=<first node's port>)`.
- Website links: no template.

`tests/test_end_to_end.py` runs the example and generated apps behind the
gateway, for GET and POST.

---

## 9. Proxy Route

```
Browser:  GET/POST /available_services/<service_name>/proxy/<widget_id>?node=<ipv6>:<port>&...
Portal → gateway:  <METHOD> https://<gateway>/relay<endpoint>?...
                   X-Gateway-Secret, X-Target-Host, X-Target-Port,
                   X-Fabric-User-Uuid, X-Fabric-User-Name, and
                   Authorization: Bearer <user's FABRIC service token>
Gateway → node:    <METHOD> http://[<ipv6>]:<port><endpoint>?...
```

Portal (`portal/routes/proxy.py`, `portal/gateway_client.py`):
1. The session must have `auth_<service_name>` set.
2. Look up the service and widget; refuse website links. Load the user's token from `user_tokens` (401 "log in again" if missing). Select the target node from `node` (validated against the service's registered nodes; defaults to the first).
3. Send the request to the gateway with the target, the consumer's identity and the user's token. **Browser headers and cookies are not forwarded.**
4. Log the call to `usage_log`. Return the body with `Content-Type: application/json`.

Gateway (`gateway/gateway.py`):
1. Check the caller IP and shared secret.
2. Check the target is inside `GATEWAY_ALLOWED_TARGETS` and the port is valid.
3. Forward only `Content-Type`, `Accept`, `Authorization`, `X-Fabric-User-*`. No redirects are followed.

Timeouts: gateway → node `GATEWAY_REQUEST_TIMEOUT` (10s), portal → gateway `GATEWAY_TIMEOUT` (15s). Network errors return 502.

Widget endpoints are validated at registration: they must be plain paths starting with `/` (letters, digits, `-`, `_`, `.`, `/`), not `/` alone, with no `..` or `//`. The generated app turns the path into a valid Python function name (e.g. `/v1/2d-scan` → `v1_2d_scan`).

---

## 10. Route Map

| Route | Auth | Purpose |
|---|---|---|
| `GET /` | None | Landing page |
| `GET /login` | None | Login page (Sign in with FABRIC button, link to `/token_test`) |
| `POST /auth/login` | None | Log in with a FABRIC service token (used by `/token_test`) |
| `GET /token_test` | None | For testing: paste a FABRIC service token, see what the portal gets from it, log in with it |
| `POST /api/token_test` | None | Check a token with FABRIC UIS without logging in |
| `POST /auth/logout` | Session | Clear session |
| `GET /register_service` | Author | Widget builder |
| `POST /api/register_service` | Author | Save new service |
| `POST /api/preview_template` | Author | Live template preview |
| `GET /edit_service/<name>` | Owner | Widget builder in edit mode |
| `POST /api/update_service` | Owner | Update existing service |
| `GET /register_service/success/<name>` | Author | Success page + template |
| `GET /my_services` | Author | Owner's services list |
| `PUT /api/services/<name>` | Owner or admin | (legacy) Update service |
| `DELETE /api/services/<name>` | Owner or admin | Delete service |
| `GET /available_services` | None | Gallery |
| `GET /available_services/<name>` | None (token gate) | Service page |
| `GET/POST /available_services/<name>/proxy/<widget_id>` | Service session | Proxy to backend |
| `GET /admin` | Admin | Admin dashboard |
| `POST /admin/users/<id>/toggle_active` | Admin | Enable/disable user |
| `POST /admin/users/<id>/set_role` | Admin | Change user role |
| `GET /admin/auth_log` | Admin | Auth log viewer |
| `GET /admin/usage_log` | Admin | Usage log viewer |

Gateway routes (only reachable from the portal):

| Route | Purpose |
|---|---|
| `GET /health` | Used by `flask check-gateway` |
| `* /relay/<path>` | Forward to `X-Target-Host`:`X-Target-Port` |

---

## 11. Deployment

Each component has its own folder, config file and deploy script:

| Component | Config | Deploy | systemd unit | Gunicorn bind |
|---|---|---|---|---|
| `portal/` (Ubuntu or Rocky) | `portal.env` | `portal/deploy.sh` | `fabric-portal` | `127.0.0.1:5000` |
| `gateway/` (Ubuntu) | `gateway.env` | `gateway/deploy.sh` | `fabric-gateway` | `127.0.0.1:5001` |

Both put Nginx in front for TLS. `flask check-gateway` on the portal verifies
the connection end to end. See `SETUP.md` for full instructions.

### Service Node Requirements

Service authors need the following on their slice nodes:
- Python 3.9+ with Flask and requests (`pip install flask requests`)
- Any system tools their service invokes
- Open firewall port for the Flask app (typically 8080) on the FABnetv6 interface
- Flask app binds to `::` (all IPv6 addresses) so the gateway can reach it over FABnetv6

---

## 12. Security Considerations

- **Token validation** — every token is validated against FABRIC UIS on login. Only `service` tokens are accepted. The only stored tokens are in `user_tokens` (see below).
- **Gateway isolation** — the gateway accepts calls only from the portal's IP with the shared secret, and forwards only to FABnetv6. It holds no user data.
- **No header leakage** — browser cookies and headers are never forwarded to service nodes; they receive only the user's service token and identity.
- **User tokens** — kept server-side in `user_tokens`, never in the cookie; deleted at logout and expired with the session (4 h). FABRIC tokens may expire sooner; nodes then return 401 and the user logs in again.
- **Website links** — their URLs must be `http(s)://`; pages open them with `rel="noopener"`.
- **Project membership** — two-level auth ensures consumers can only use services they're authorized for (per-service allowed-project list).
- **Ownership checks** — edit/delete service endpoints verify `owner_id == session user_id` (or admin role).
- **Request logging** — every auth attempt and every widget invocation is logged with IP address, timestamp, user, and parameters for audit trail.
- **Self-signed certificate** — the public-facing HTTPS uses a self-signed cert (no domain). Browsers show a warning; this is expected for a testbed deployment.
- **Admin bootstrapping** — the first admin is seeded via the `flask seed-admin <uuid>` CLI command.

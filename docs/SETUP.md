# Deploying the FABRIC Service Portal

Three kinds of machine are involved. Set them up in this order:

1. **Gateway**: a FABRIC VM with FABNetv4Ext (public IPv4) and FABnetv6
2. **Portal**: a VM with a public IP (here also a FABRIC VM with FABNetv4Ext)
3. **Service nodes**: each service author's own slice VMs, with FABnetv6

| Machine | Supported OS |
|---|---|
| Gateway | Ubuntu 22.04 / 24.04 |
| Portal | Ubuntu 22.04 / 24.04, or Rocky Linux 8 / 9 (see [Portal on Rocky Linux](#portal-on-rocky-linux)) |
| Service nodes | any Linux with Python 3.9+ |

In the commands below, `gateway$` means "run on the gateway VM", `portal$` on
the portal VM, and `notebook` in your FABRIC JupyterHub notebook.

---

## 0. Before you start

### 0a. The slice

| VM | Image | Network services |
|---|---|---|
| gateway | Ubuntu | FABNetv4Ext (public IPv4), FABnetv6 |
| portal | Ubuntu or Rocky | FABNetv4Ext (public IPv4) |

Make each FABNetv4Ext address publicly routable (`make_ip_publicly_routable`,
then `slice.submit()`), and allow inbound TCP 443 to it.

### 0b. Routes on both VMs

On FABRIC VMs, traffic leaves through the management interface by default, so
the public IP needs its own routes. Set them up from the notebook, and make
them re-apply at boot (a small systemd service running the commands below).
**Without that, they are lost on every reboot.**

On **each** VM (fill in its own values; `ip -br -4 addr` shows the interface
holding the public IP, and the network's gateway is `net.get_gateway()` in the
notebook):

```bash
IFACE=<public interface>  PUBLIC_IP=<this VM's public IP>  PREFIX=<prefix length>  GW=<FABNetv4Ext gateway>
sudo ip link set dev $IFACE up
sudo ip addr replace $PUBLIC_IP/$PREFIX dev $IFACE
sudo ip route replace default via $GW dev $IFACE table 102
sudo ip rule add from $PUBLIC_IP table 102 priority 100
```

On the **portal** only, also send traffic for the gateway out of the public
interface, so the gateway sees the portal's public IP:

```bash
sudo ip route replace <gateway public IP>/32 via $GW dev $IFACE src $PUBLIC_IP
```

On the **gateway** only, route FABnetv6 through its FABnetv6 interface
(notebook: `node.ip_route_add(subnet=fablib.FABNETV6_SUBNET, gateway=<fabnetv6 gw>)`).

Check on the portal:

```bash
portal$ ip route get <gateway public IP>     # want: dev <public iface> src <portal public IP>
```

### 0c. Get the code on both VMs

Clone the repository into the home directory of each VM:

```bash
sudo apt install -y git        # Ubuntu, if git is missing
sudo dnf install -y git        # Rocky Linux (portal), if git is missing
git clone https://github.com/fabric-testbed/fabric_service_marketplace.git ~/fabric_service_marketplace
ls ~/fabric_service_marketplace       # README.md  docs  gateway  portal  service_node  tests
```

Or from the notebook, for both VMs at once:

```python
for node in (portal_node, gateway_node):
    node.execute('command -v git || sudo apt install -y git || sudo dnf install -y git; '
                 'git clone https://github.com/fabric-testbed/fabric_service_marketplace.git '
                 '~/fabric_service_marketplace && ls ~/fabric_service_marketplace')
```

### 0d. Generate the shared secret

Once, anywhere. It goes into both config files:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 1. Gateway

```bash
gateway$ cd ~/fabric_service_marketplace/gateway
gateway$ cp gateway.env.example gateway.env
gateway$ nano gateway.env
```

| Setting | Value |
|---|---|
| `GATEWAY_PUBLIC_IP` | the gateway's FABNetv4Ext public IP |
| `GATEWAY_SHARED_SECRET` | the secret from 0d |
| `GATEWAY_ALLOWED_SOURCES` | `<portal public IP>/32` |

```bash
gateway$ sudo bash deploy.sh
```

It installs Nginx and a `fabric-gateway` systemd service, and makes a
self-signed TLS certificate (`gateway.crt`). It should end with
`Nginx: running` and `Gateway: running`.

`GATEWAY_ALLOWED_SOURCES` is enforced twice: Nginx refuses other callers
(`/etc/nginx/fabric-gateway-allow.conf`, generated; don't edit it by hand), and
`gateway.py` checks it again with the shared secret. After changing
`gateway.env`, run `sudo bash restart.sh`.

---

## 2. Portal

```bash
portal$ cd ~/fabric_service_marketplace/portal
portal$ cp portal.env.example portal.env
portal$ nano portal.env
```

| Setting | Value |
|---|---|
| `PORTAL_SERVER_NAME` | the portal's hostname, or its public IP |
| `PORTAL_PROJECT_ID` | UUID of the FABRIC project whose members may register services |
| `GATEWAY_URL` | `https://<gateway public IP>` |
| `GATEWAY_SHARED_SECRET` | the same secret |
| `GATEWAY_TLS_VERIFY` | `false` for testing, or the path to a copy of `gateway.crt` (see 2c) |

```bash
portal$ sudo bash deploy.sh
```

It installs Nginx and a `fabric-portal` systemd service, makes a self-signed
TLS certificate, and ends by checking the connection to the gateway.

### 2a. Check the connection to the gateway

Run any time:

```bash
portal$ cd ~/fabric_service_marketplace/portal
portal$ sudo FLASK_APP=app.py venv/bin/flask check-gateway
```

`OK: {'allowed_targets': [...], 'status': 'ok'}` means it works. With
`GATEWAY_TLS_VERIFY=false` you also see an `InsecureRequestWarning`; that is
expected.

If it fails, see [Troubleshooting](#troubleshooting).

### 2b. Make yourself admin

```bash
portal$ sudo FLASK_APP=app.py venv/bin/flask seed-admin "<your FABRIC UUID>"
```

### 2c. Optional: check the gateway's certificate

Instead of `GATEWAY_TLS_VERIFY=false`, let the portal trust exactly the
gateway's certificate:

```bash
gateway$ cat ~/fabric_service_marketplace/gateway/gateway.crt      # copy the output
portal$  nano ~/fabric_service_marketplace/portal/gateway.crt      # paste it
```

Set `GATEWAY_TLS_VERIFY=/home/ubuntu/fabric_service_marketplace/portal/gateway.crt`
(`/home/rocky/...` on Rocky),
run `sudo bash restart.sh`, then `check-gateway` again.

If the gateway VM is rebuilt, it gets a new certificate and the copy must be
redone. To avoid that, keep `gateway.crt` and `gateway.key` and put them back in
`gateway/` before running `deploy.sh` on the new VM.

### 2d. Open the portal

Go to `https://<portal public IP>/` and accept the browser's certificate
warning (the certificate is self-signed; for a real hostname use certbot).

To log in, click **Login**, then **For testing: log in with a FABRIC service
token**. Paste a **service** token from the FABRIC portal (resource tokens are
refused), click **Check token**, then **Log in with this token**.

The portal needs outbound HTTPS to `https://uis.fabric-testbed.net` for this.
Check with `curl -sI https://uis.fabric-testbed.net | head -1` on the portal.

---

## 3. Service authors

### App on my FABRIC nodes

On each VM in the author's slice, with FABnetv6 attached:

```bash
sudo apt update && sudo apt install -y python3 python3-venv
python3 -m venv venv && source venv/bin/activate
pip install flask requests
python app.py         # the code shown after registering
```

- The app listens on `::` so the gateway can reach it over FABnetv6; open its
  port (e.g. 8080) in the VM's firewall.
- Register the node's **FABnetv6** address in the portal.
- The app checks each request's `Authorization: Bearer` token with FABRIC and
  requires a service token from one of the allowed projects (built in).
- Users can also call it directly from their own VMs:
  `curl -H "Authorization: Bearer $FABRIC_TOKEN" "http://[<node ipv6>]:8080/api/service_config?..."`

Reference app: `service_node/example_app.py`, run with
`ALLOWED_PROJECTS=<uuid>,<uuid> python example_app.py`.

**Already have a REST API on the node?** You don't need the generated app. On
the registration form, change **Endpoint on your node** from
`/api/service_config` to your API's path (e.g. `/v1/measure`) and pick GET or
POST. The portal sends the form's inputs there (as query parameters for GET, a
JSON body for POST) with `Authorization: Bearer <user's FABRIC service token>`,
and shows the JSON you return. Your API must check that token, the way
`require_auth` in the generated app does: refuse tokens whose `scope` isn't
`service`, check the token with FABRIC (`https://uis.fabric-testbed.net`), and
require one of your allowed projects.

### External web service

Enter the service's URL. The portal lists it and links to it; the service
handles its own login. Nothing to install.

---

## 4. Adding the automated login (e.g. CILogon)

Today users log in on `/token_test` by pasting a FABRIC service token. For
production, the **Sign in with FABRIC** button on `/login` should start an
automated login instead. The portal is built so that this only needs:

| Change | Where |
|---|---|
| Add two routes: one that starts the login, one that receives the result | `portal/routes/auth.py` |
| Point the Sign in button at the start route | `PORTAL_LOGIN_URL` in `portal.env` |
| Register the callback URL with the identity provider | e.g. `https://<portal>/auth/fabric/callback` |

**Nothing else changes.** The one function to call is
`login_with_fabric_token(token)` in `portal/routes/auth.py`. Given the user's
FABRIC service token, it already does everything else:

- refuses tokens that aren't `service` scope
- checks the token with FABRIC (`FABRIC_UIS_URL`) and gets the user and **all** their projects
- decides whether they may register services (`PORTAL_PROJECT_ID`) and which services they may use
- stores the token server-side, for passing to service nodes
- starts the portal session

It returns `(user, None)` on success or `(None, error_message)` on failure.

### What the login flow must produce

A **FABRIC service token**, the same kind users paste on `/token_test`. A
token from a CILogon client of your own is not enough: it has no FABRIC
`scope` or projects, so `login_with_fabric_token` rejects it. After CILogon,
the flow has to get the user's service token from FABRIC's Credential Manager
(ask the FABRIC team for the supported way to do this for a portal).

### Example

Add to `portal/routes/auth.py` (also add `import secrets` and
`from urllib.parse import quote` at the top, and `url_for` to the
`from flask import ...` line). The two `...` lines are the parts specific to
the identity provider:

```python
@auth_bp.route('/auth/fabric/start')
def fabric_login_start():
    # Remember where to return to, and a random state to check on the way back
    session['login_state'] = secrets.token_urlsafe(16)
    session['login_next'] = safe_next(request.args.get('next'), '')
    callback = url_for('auth.fabric_login_callback', _external=True)
    login_url = ...  # the identity provider's login URL, with `callback` and session['login_state']
    return redirect(login_url)


@auth_bp.route('/auth/fabric/callback')
def fabric_login_callback():
    # Read these BEFORE logging in: login_with_fabric_token() clears the session
    expected_state = session.pop('login_state', None)
    next_url = session.pop('login_next', '')
    if not expected_state or request.args.get('state') != expected_state:
        return redirect('/login?error=' + quote('Login expired, please try again'))

    fabric_token = ...  # exchange request.args['code'] for the user's FABRIC service token

    user, error = login_with_fabric_token(fabric_token)
    if error:
        return redirect('/login?error=' + quote(error))
    return redirect(next_url or _default_landing(user))  # back to the page they came from
```

Then in `portal.env`:

```
PORTAL_LOGIN_URL=/auth/fabric/start
```

and `sudo bash restart.sh`. The login page's **Sign in with FABRIC** button
becomes active and goes to `/auth/fabric/start?next=<page to return to>`.

Put any client ID or secret the identity provider needs in `portal.env`, and
read it in `portal/config.py` like the other settings.

### Checking it

- `tests/test_end_to_end.py::test_login_with_fabric_token` checks the contract
  above (full project list, permissions, session, stored token).
- After logging in, open a service you are allowed to use: its widget should
  work, which means your token reached the service node.
- `/token_test` and its "Log in with this token" button keep working alongside
  the new login, which is useful for testing. To turn them off in production,
  remove the `/token_test`, `/api/token_test` and `/auth/login` routes from
  `portal/routes/auth.py` and the "For testing" link from `templates/login.html`.

If a FABRIC token expires before the portal's 4-hour session, calls to service
nodes fail until the user logs in again (token refresh is not implemented).

---

## Updating

Pull the new code and restart:

```bash
portal$  cd ~/fabric_service_marketplace && git pull && cd portal  && sudo bash restart.sh
gateway$ cd ~/fabric_service_marketplace && git pull && cd gateway && sudo bash restart.sh
```

Your local files are not in the repository (see `.gitignore`), so `git pull`
leaves them alone: `portal.env`, `gateway.env`, the database
(`portal/instance/`), the secret key (`portal/.secret_key`), the certificates
and the `venv` folders. `restart.sh` keeps them too, and picks up `.env` changes.

If a later version changes `deploy.sh` or the Nginx config, run
`sudo bash deploy.sh` instead of `restart.sh`; it keeps the same files.

On start-up the portal upgrades an older database: missing columns are added,
and services from the old API-key mode become "App on my FABRIC nodes"
services (their authors must run the newly generated app).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `check-gateway`: `Network is unreachable` | The portal has no route to the gateway | Re-apply the routes (0b) on the portal; check `ip route get <gateway IP>` |
| `check-gateway`: `ConnectTimeout` | Routes missing on the gateway, or port 443 blocked | Re-apply the routes (0b) on the gateway. Watch `sudo tcpdump -ni <public iface> tcp port 443` on the gateway while retrying |
| `check-gateway`: `SSLError` | `GATEWAY_TLS_VERIFY` doesn't match the gateway's current `gateway.crt` | Copy it again (2c), or use `false` for testing |
| HTML `403 Forbidden` from Nginx, or `caller IP not allowed` | `GATEWAY_ALLOWED_SOURCES` isn't the IP the portal's requests come from | On the gateway, `sudo grep "forbidden by rule" /var/log/nginx/error.log \| tail -1` shows it after `client:`. Put it in `gateway.env`, `sudo bash restart.sh` |
| `invalid shared secret` | The two secrets differ | Make them identical (no quotes or spaces), `sudo bash restart.sh` on the side you changed |
| `curl https://<gateway>/health` from the portal gives `invalid shared secret` | Expected: curl doesn't send the secret | Use `check-gateway` instead |
| Login: `This is a FABRIC 'resource' token` | Resource tokens are refused | Create a **service** token on the FABRIC portal |
| Login / token test can't reach FABRIC | No outbound HTTPS to `uis.fabric-testbed.net` | Check the portal's routes and DNS |
| Everything broke after a reboot | Routes (0b) were not re-applied | Make them re-apply at boot (0b) |
| `deploy.sh`: `Replace the changeme values` | The `.env` file still has placeholders | Fill them in |

---

## Portal on Rocky Linux

`portal/deploy.sh` detects Rocky Linux (8 or 9; also Alma/RHEL) and does the
same steps the Rocky way. The steps above are unchanged; only these differ, and
the script handles them:

| | Ubuntu | Rocky Linux |
|---|---|---|
| Packages | `apt install python3 python3-venv nginx sqlite3 openssl` | `dnf install python3 nginx sqlite openssl` |
| Python | 3.10+ | Rocky 9: 3.9, works. Rocky 8: 3.6 is too old, so `python3.11` is installed and used for the venv |
| Nginx site config | `/etc/nginx/sites-available/portal`, linked in `sites-enabled/` | `/etc/nginx/conf.d/fabric-portal.conf` |
| Nginx default site | removed from `sites-enabled/` | `default_server` removed from `/etc/nginx/nginx.conf` (backup kept as `nginx.conf.bak`) |
| TLS certificate | read from `portal/` | copied to `/etc/nginx/ssl/`, because SELinux stops Nginx reading files under `/home` |
| SELinux | not used | `setsebool -P httpd_can_network_connect 1` (lets Nginx reach the app), and `chcon -t bin_t` on `venv/bin` (lets systemd start Gunicorn from `/home`) |
| Firewall | none by default | if `firewalld` is running, http and https are opened |
| Default user | `ubuntu` | `rocky` (paths are `/home/rocky/...`) |

The Python code, `portal.env`, `restart.sh`, `check-gateway` and the routes in
0b are the same on both.

If you replace `portal.crt` / `portal.key` (e.g. with a real certificate), run
`sudo bash deploy.sh` again on Rocky so the copies in `/etc/nginx/ssl/` are
updated.

If the portal won't start on Rocky, look for SELinux denials with
`sudo ausearch -m avc -ts recent`, and check `sudo journalctl -u fabric-portal -n 50`.
The gateway is only supported on Ubuntu.

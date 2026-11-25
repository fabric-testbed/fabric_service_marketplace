# Deploying the FABRIC Service Marketplace

Three kinds of machine are involved. Set them up in this order:

1. **Gateway**: a FABRIC VM with FABNetv4Ext (public IPv4), FABnetv6 and FABNetv4
2. **Portal**: a VM with a public IP (here also a FABRIC VM with FABNetv4Ext)
3. **Service nodes**: each service author's own slice VMs, with FABnetv6 or FABNetv4

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
| gateway | Ubuntu | FABNetv4Ext (public IPv4), FABnetv6, FABNetv4 |
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
(notebook: `node.ip_route_add(subnet=fablib.FABNETV6_SUBNET, gateway=<fabnetv6 gw>)`),
and FABNetv4 through its FABNetv4 interface:

```bash
V4IF=<FABNetv4 interface>  V4IP=<this VM's FABNetv4 address>/<prefix>  V4GW=<FABNetv4 gateway>
sudo ip link set dev $V4IF up
sudo ip addr replace $V4IP dev $V4IF
sudo ip route replace 10.128.0.0/10 via $V4GW dev $V4IF
```

The gateway needs its own FABNetv4 interface for this: FABNetv4 nodes do not
answer its public FABNetv4Ext address. Leave FABNetv4 out (and out of
`PORTAL_SERVICE_NODE_NETWORK` and `GATEWAY_ALLOWED_TARGETS`) if no service will
use it. To add FABNetv4 to a gateway slice that is already running, add the
network card first (`node.add_component(model='NIC_Basic', ...)`,
`slice.submit()`), then the network on that card in a second
`slice.submit()`; asking for both at once has failed.

To keep these addresses and routes across a reboot, put them in a netplan file
on the VM (`/etc/netplan/*.yaml`, checked with `sudo netplan generate`).

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

(To run the portal as Docker containers instead, see [Portal in Docker](#portal-in-docker).)

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

**Sign in with FABRIC** is set up separately, in [section 4](#4-sign-in-with-fabric).
Before that, click **Login**, then **For testing: log in with a FABRIC service
token**. Paste a **service** token from the FABRIC portal (resource tokens are
refused), click **Check token**, then **Log in with this token**.

The portal needs outbound HTTPS to `https://uis.fabric-testbed.net` for this.
Check with `curl -sI https://uis.fabric-testbed.net | head -1` on the portal.

---

## 3. Service authors

### App on my FABRIC nodes

The author fills in the registration form: the service's name (free text; the
address of its page, e.g. `fabric-llm-service`, is filled in from it and cannot
be changed later), its service project (picked by name from the service
projects the author owns), its nodes, and the widget (endpoint, method, input
and output fields). Problems are shown on the field they belong to. The portal
then shows a Flask app built from that form. On each VM in the author's slice,
with FABnetv6 or FABNetv4 attached:

```bash
sudo apt update && sudo apt install -y python3 python3-venv
python3 -m venv venv && source venv/bin/activate
pip install flask requests
python app.py         # the code shown after registering
```

- The app listens on `::` (IPv6 and IPv4) so the gateway can reach it; open its
  port (e.g. 8080) in the VM's firewall.
- Register the node's **FABnetv6** address (`2602:fcfb:…`) or **FABNetv4**
  address (`10.128.…`) in the portal.
- The gateway calls nodes with plain HTTP. An app that only answers HTTPS, or
  that wants its own API key instead of a FABRIC token, cannot be a node
  service: register it as a website link (below).

The generated app (and the reference `service_node/example_app.py`) has four
parts:

| Part | | What it does |
|---|---|---|
| 1. Setup | keep | Creates the Flask app |
| 2. Health check, `GET /health` | keep | Returns `{"status": "ok"}` without a token, so anyone can check the app is running |
| 3. FABRIC token check | **optional** | Checks each request's FABRIC service token with FABRIC and requires one of the allowed projects. Switch: `CHECK_FABRIC_TOKEN` |
| 4. Your endpoints | **fill in** | One route per widget; fill in the lines marked `>>> FILL IN <<<` |

**Should the token check stay on?** The portal already logs users in and checks
the service's project before forwarding a request. But the node's
FABnet address can be reached from any FABRIC slice, and direct callers skip
the portal.

- **On (default):** only FABRIC users in the allowed projects can call the
  app, through the portal or directly from their own VMs
  (`curl -H "Authorization: Bearer $FABRIC_TOKEN" "http://[<node ipv6>]:8080/..."`),
  and `caller()` tells the app reliably who each caller is, e.g. to record who
  used what.
- **Off** (`CHECK_FABRIC_TOKEN = False`): anyone on FABnet can call the app.
  `caller()` then reads the `X-Fabric-User-Uuid/Name` headers the portal adds:
  fine for display, but a direct caller can fake them. Use this only if that
  is acceptable for the service.

Reference app: `service_node/example_app.py`, run with
`ALLOWED_PROJECTS=<uuid>,<uuid> python example_app.py`
(add `CHECK_FABRIC_TOKEN=false` to turn the check off).

**Already have a REST API on the node?** You don't need the generated app. On
the registration form, change **Endpoint on your node** from
`/api/service_config` to your API's path (e.g. `/v1/measure`) and pick GET or
POST. The portal sends the form's inputs there (as query parameters for GET, a
JSON body for POST) with `Authorization: Bearer <user's FABRIC service token>`
and `X-Fabric-User-Uuid/Name`, and shows the JSON you return. Add a
`GET /health` endpoint, and, if you want the token check, do what
`require_auth` in the generated app does: refuse tokens whose `scope` isn't
`service`, check the token with FABRIC (`https://uis.fabric-testbed.net`), and
require one of your allowed projects. `/health` can't be used as a widget
endpoint.

The service should explain its own use, e.g. on a page of its own or in the
author's docs.

### Website link (approved by an admin)

For a service that already has its own public address and its own login or
tokens. On the registration form choose **Website link**, pick the service
project it belongs to (one you own) and enter its web address. Nothing to install.

The request is saved as *Waiting for approval*: it is not listed, and only its
owner and the admins can open its page. An admin opens the web address, then
clicks **Approve** or **Reject** under "Website links waiting for approval" on
the Admin page (make someone admin with `seed-admin` or on the Admin page).
Once approved, members of the service project get the link; everyone else sees
the service with "Request access". Changing the web address later sends it back
for approval; other edits do not.

A link added by an admin is approved in the same way, explicitly, on the Admin
page (an admin may approve their own), and needs a service project like any other.

---

## 4. Sign in with FABRIC

The **Sign in with FABRIC** button on `/login` uses the same login as FABRIC's
Credential Manager: **vouch-proxy** sends the browser to CILogon and sets the
FABRIC login cookie. The portal then asks FABRIC who the cookie belongs to, and
later asks the Credential Manager for the service tokens it passes to service
nodes. The button stays disabled until the steps below are done; until then
log in on `/token_test`.

```
Browser ─► /auth/fabric/start ─► /vouch/login ─► CILogon ─► /auth ─► /auth/<state>/
        (vouch sets the cookie) ─► /auth/fabric/callback ─► logged in
```

### What you need

| | |
|---|---|
| Docker on the portal VM | to run vouch-proxy |
| A CILogon client | callback URL `https://<portal hostname>/auth` (register at https://cilogon.org/oauth2/register) |
| FABRIC's vouch settings | `jwt.secret`, cookie name and cookie domain of the FABRIC deployment (from the FABRIC team) |
| A hostname inside the cookie's domain | e.g. `something.fabric-testbed.net`; an IP address does not work |

**Why FABRIC's vouch settings:** the portal does not check the login cookie. It
passes it to FABRIC's user service and Credential Manager, and they only accept
a cookie signed with their own secret. A different secret under the same cookie
name would also overwrite users' FABRIC login cookie with one FABRIC's own
sites cannot read.

### 4a. Start vouch-proxy

```bash
portal$ cd ~/fabric_service_marketplace/portal/vouch
portal$ cp config.example config
portal$ nano config            # fill in every CHANGE_ME value
portal$ chmod 600 config
portal$ docker compose up -d
portal$ docker logs portal-vouch-proxy | tail -5
```

(With [Portal in Docker](#portal-in-docker), vouch-proxy is part of
`portal/docker-compose.yml`: fill in `vouch/config` and run
`docker compose restart vouch-proxy` in `portal/` instead.)

It listens on `127.0.0.1:9090` only. The portal's Nginx forwards `/auth`,
`/auth/<state>/` (where vouch finishes the login), `/vouch/login` and
`/vouch/logout` to it; `deploy.sh` installs that Nginx
config, so run `sudo bash deploy.sh` once if the portal was deployed with an
older version.

### 4b. Turn the button on

In `portal.env`:

```
PORTAL_LOGIN_URL=/auth/fabric/start
# only if they differ from the defaults:
# PORTAL_VOUCH_COOKIE_NAME=fabric-service
# FABRIC_CREDMGR_URL=https://cm.fabric-testbed.net
```

then `sudo bash restart.sh`.

### 4c. Check it

1. Open `https://<portal>/login` and click **Sign in with FABRIC**. After
   CILogon you should land on the portal, logged in.
2. **Available Services** shows your FABRIC service projects at the top. The
   services open to them are usable; the rest are greyed out.
3. Open a service and use its widget. The node receives a service token for
   the service project that gives you access; that token also appears on the
   Credential Manager's token page with the comment `FABRIC Service Marketplace: <service>`.

| Symptom | Cause |
|---|---|
| Back on `/login` with "Your FABRIC sign-in has expired" right after CILogon | vouch's `jwt.secret` or cookie name is not FABRIC's, so FABRIC's user service refuses the cookie |
| `502` on `/vouch/login` | vouch-proxy is not running (`docker ps`) |
| vouch shows an error instead of going to CILogon | the portal's hostname is outside `vouch.cookie.domain`, or the CILogon callback URL is not `https://<portal>/auth` |
| Widget: "FABRIC did not issue a service token for this project" | the Credential Manager refused; see `sudo journalctl -u fabric-portal -n 50` |
| After CILogon: "Not Found" at `/auth/<long code>/?code=...` | Nginx does not forward `/auth/<state>/` to vouch-proxy: use the current `portal/nginx/default.conf` (Docker) or `portal.conf`, and reload Nginx |
| Widget: "Your FABRIC sign-in has expired" | the login cookie ran out (vouch `maxAge`); log out and sign in again |

### Who can use what

- Only FABRIC **service projects** give access to services, because FABRIC
  issues service tokens for those only. Every service belongs to exactly one
  service project.
- Only an **owner, lead or creator** of a service project can register a
  service under it (they must also be a member of `PORTAL_PROJECT_ID`, or an
  admin). Ownership is read from FABRIC at sign-in, so someone newly made an
  owner logs out and signs in again.
- A **website link** is listed only after an admin approves it.
- Every service is listed. The ones a user cannot use yet are greyed out with
  **Request access**; the service's page links to its project on the
  FABRIC portal (`FABRIC_PORTAL_URL`), where the user asks to join. After being
  added they log out and sign in again.
- Logging out of the portal also ends the FABRIC sign-in (clears the login
  cookie), like the Credential Manager's logout. To leave it alone, set
  `PORTAL_VOUCH_LOGOUT_URL=` (empty) in `portal.env`.
- The portal session lasts 4 hours. A service token lasts
  `PORTAL_SERVICE_TOKEN_HOURS` (default 4) and is requested again when it
  runs out, as long as the login cookie is still valid.

`/token_test` and its "Log in with this token" button keep working alongside
the sign-in, which is useful for testing. To turn them off in production,
remove the `/token_test`, `/api/token_test` and `/auth/login` routes from
`portal/routes/auth.py` and the "For testing" link from `templates/login.html`.

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

---

## Portal in Docker

Instead of `deploy.sh`, the portal can run as containers, laid out like the
other FABRIC services: Nginx in front, the portal app, and vouch-proxy for the
FABRIC sign-in. Use one or the other on a machine, not both: they use the
same ports. The gateway is not part of this; it stays as in section 1.

| Container | Image | Does |
|---|---|---|
| `marketplace-nginx` | `nginx:1` | HTTPS on ports 80/443 (`nginx/default.conf`, `nginx/nginx.conf`) |
| `marketplace-portal` | built from `portal/Dockerfile` | the portal app (Gunicorn, as the user `portal`) |
| `marketplace-vouch-proxy` | `fabrictestbed/vouch-proxy` | the FABRIC sign-in (section 4) |

Everything is in `portal/`; these files are yours and are not in the repository:

| File | What |
|---|---|
| `portal.env` | as in section 2 |
| `ssl/fullchain.pem`, `ssl/privkey.pem` | the portal's TLS certificate (with its chain) and key |
| `vouch/config` | copy of `vouch/config.example`, filled in (section 4) |
| `instance/` | created on first start: the database and the secret key. Put the gateway's certificate here as `instance/gateway.crt` |

```bash
portal$ cd ~/fabric_service_marketplace/portal
portal$ cp portal.env.example portal.env && nano portal.env
portal$ mkdir -p ssl instance        # then put the certificate, key and gateway.crt in place
portal$ cp vouch/config.example vouch/config && nano vouch/config && chmod 600 vouch/config
portal$ docker compose up -d --build
portal$ docker compose ps
```

In `portal.env`, the gateway's certificate is given by its path inside the
container: `GATEWAY_TLS_VERIFY=/usr/src/app/instance/gateway.crt`.

The portal starts even while `vouch/config` is not filled in; only **Sign in
with FABRIC** needs it. Don't also start `vouch/docker-compose.yml`: that one
is for the `deploy.sh` setup.

| Task | Command (in `portal/`) |
|---|---|
| Check the connection to the gateway | `docker compose exec -u portal portal flask check-gateway` |
| Make someone admin | `docker compose exec -u portal portal flask seed-admin "<FABRIC UUID>"` |
| Logs | `docker compose logs -f portal` (or `nginx`, `vouch-proxy`) |
| After changing `portal.env` | `docker compose up -d` |
| After changing `vouch/config` | `docker compose restart vouch-proxy` |
| After replacing the certificate | `docker compose restart nginx` |
| Update the code | `git pull && docker compose up -d --build` |

**Moving from `deploy.sh` to Docker** on the same machine: copy `portal.crt` /
`portal.key` to `ssl/fullchain.pem` / `ssl/privkey.pem`, copy `.secret_key` and
`gateway.crt` into `instance/` (the database is already there), change
`GATEWAY_TLS_VERIFY` as above, then
`sudo systemctl disable --now fabric-portal nginx` and `docker compose up -d --build`.

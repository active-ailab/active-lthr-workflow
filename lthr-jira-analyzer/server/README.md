Owner: cs-dongqi@zepp.com
Organization: Active.Bu

# LTHR Jira Attachment Service

The service accepts a Jira server address, username, and password from the browser, signs in through Jira's cookie-based session endpoint, and uses that session to retrieve issue attachments. ZIP files are expanded recursively; matching logs and FIT files are then made available directly to the browser analyzer. Log parsing and CSV generation remain in the browser.

Log discovery prefers `.log` and `.txt` files whose names start with `mili`, `ios`, or `amazfit` (case-insensitive). If no preferred log is found, it falls back to all `.log` and `.txt` files, excluding the exact name `hmble_log.log`. Logs with identical contents are deduplicated by SHA-256. FIT discovery uses the `.fit` suffix.

The Jira password is sent to this service only for login and is not saved. The service keeps the Jira session cookie in memory for up to eight hours. The browser keeps an opaque service session token in `sessionStorage`. The “记住登录信息” checkbox saves only the Jira address and username in browser `localStorage`. Restarting the service clears active sessions.

This login method works when the Jira instance permits username/password cookie authentication. Jira accounts that require SSO or two-step verification may need a different authentication flow. Jira and its administrator control which methods are available.

## Run locally

Python 3.8+ and the Python standard library are sufficient. Jira CLI and local Jira credentials are not used.

```bash
cd lthr-jira-analyzer/server
python3 app.py
```

The service listens on `127.0.0.1:8080` by default and can be started without Jira credentials or additional secret variables. Check startup with:

```bash
curl http://127.0.0.1:8080/health
```

For a local browser trial, serve the repository root in a second terminal:

```bash
python3 -m http.server 8000
```

Open `http://127.0.0.1:8000/`. The page sends login and attachment requests to `http://127.0.0.1:8080/api/v1`. Do not open the page with `file://`.

The deployed GitHub Pages page sends requests to `https://www.active-ai.cloud/lthr-jira-api/api/v1`. Caddy must strip the `/lthr-jira-api` prefix and proxy the remaining path to this service. This separate route avoids the existing `/api/*` route to the Node service. The service must allow the GitHub Pages origin through `ALLOWED_ORIGIN`.

## Deploy on the server

This setup assumes the public hostname is `www.active-ai.cloud`, Caddy is the HTTPS reverse proxy, and the Python service runs on the same host at `127.0.0.1:8080`. Keep the Python listener bound to loopback; expose it through Caddy rather than opening port 8080 to the internet.

### 1. Install the service files

Copy the latest `lthr-jira-analyzer/server/app.py` to `/opt/lthr-jira-analyzer/server/app.py` on the server. Python 3.8+ is sufficient; there are no third-party Python packages to install.

Create a dedicated service account if one does not already exist, then create the code and temporary-file directories:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin lthr-jira
sudo install -d -o root -g root -m 755 /opt/lthr-jira-analyzer/server
sudo install -d -o lthr-jira -g lthr-jira -m 700 /var/lib/lthr-jira
```

Install the file from your uploaded/released checkout (adjust the source path to where you copied the repository):

```bash
sudo install -o root -g root -m 755 /path/to/active-lthr-workflow/lthr-jira-analyzer/server/app.py /opt/lthr-jira-analyzer/server/app.py
```

If the `lthr-jira` account already exists, skip `useradd`. When deploying a later version, repeat the `install app.py` command and restart the service.

### 2. Configure systemd

Create `/etc/systemd/system/lthr-jira-attachment.service`:

```ini
[Unit]
Description=LTHR Jira Attachment Service
After=network.target

[Service]
Type=simple
User=lthr-jira
Group=lthr-jira
WorkingDirectory=/opt/lthr-jira-analyzer/server
ExecStart=/usr/bin/python3 /opt/lthr-jira-analyzer/server/app.py
Restart=on-failure
RestartSec=3
Environment=HOST=127.0.0.1
Environment=PORT=8080
Environment=ALLOWED_ORIGIN=https://active-ailab.github.io
Environment=JIRA_ALLOWED_HOSTS=jira.huami.com
Environment=SESSION_DIR=/var/lib/lthr-jira

[Install]
WantedBy=multi-user.target
```

Load and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lthr-jira-attachment
sudo systemctl status lthr-jira-attachment --no-pager
curl http://127.0.0.1:8080/health
```

The regular health request should return HTTP 200 and `{"status": "ok", ...}`. The frontend uses `/health?return=app` to trigger the browser's certificate interstitial and then redirect back to the analyzer after the user continues. To inspect service logs, use `sudo journalctl -u lthr-jira-attachment -n 100 --no-pager`.

### 3. Add a dedicated Caddy route

In **both** the `:80` and `:443` site blocks in `/etc/caddy/Caddyfile`, add this handler alongside the existing handlers:

```caddy
handle_path /lthr-jira-api/* {
    reverse_proxy 127.0.0.1:8080
}
```

Keep the existing `handle /api/*` route to `127.0.0.1:3001`. The new prefix is separate, and `handle_path` removes `/lthr-jira-api` before forwarding. For example, the public request `/lthr-jira-api/api/v1/auth/login` reaches the Python service as `/api/v1/auth/login`; `/lthr-jira-api/health` reaches it as `/health`.

Validate and reload Caddy:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -i https://www.active-ai.cloud/lthr-jira-api/health
```

The public health request should return HTTP 200. If it returns a Node response or 404, inspect the placement of the new handler in both site blocks and ensure the Caddy configuration was reloaded.

### 4. Publish the frontend change

The repository's production frontend is configured to call `https://www.active-ai.cloud/lthr-jira-api/api/v1`; local testing still calls `http://127.0.0.1:8080/api/v1`. Commit and push the updated web assets to the GitHub Pages branch/source and wait for Pages to publish. Then open `https://active-ailab.github.io/`, sign in to Jira, and try an issue with attachments.

If the browser reports a CORS error, confirm `ALLOWED_ORIGIN` exactly matches the page origin (`https://active-ailab.github.io`), then restart `lthr-jira-attachment`. CORS allows the browser origin; Jira permissions still come from the account that signed in. The server stores Jira session cookies in process memory, so a service restart requires users to sign in again.

## Endpoints

- `GET /health` reports process status and authentication mode.
- `GET /health?return=app` redirects to the GitHub Pages analyzer after the browser accepts the service certificate.
- `POST /api/v1/auth/login` accepts `{ "baseUrl", "username", "password" }`, authenticates against Jira, and returns an opaque service session token.
- `GET /api/v1/auth/status` checks a session using `Authorization: Bearer {sessionToken}`.
- `POST /api/v1/auth/logout` removes the service session.
- `POST /api/v1/issues/{issueKey}/attachments` retrieves and processes issue attachments for the logged-in user.
- `GET /api/v1/sessions/{sessionId}/files/{fileId}` returns a staged file to the session that owns it.

ZIP files are expanded recursively before selecting logs and FITs. Candidate logs are `MILI*.log`, `MILI*.txt`, `ios*.log`, `ios*.txt`, `Amazfit*.log`, and `Amazfit*.txt`; all `.fit` files are included. Logs with identical SHA-256 content are returned once, with direct Jira attachments preferred over extracted copies. A FIT alone does not count as a matching log. Matched files are staged for 15 minutes.

## Configuration

- `HOST`: bind address; defaults to `127.0.0.1`.
- `PORT`: listen port; defaults to `8080`.
- `ALLOWED_ORIGIN`: comma-separated exact browser origins; defaults to the GitHub Pages origin and local test origins.
- `JIRA_ALLOWED_HOSTS`: comma-separated Jira host allowlist; defaults to `jira.huami.com`. Use hostnames only, without scheme or paths.
- `SESSION_DIR`: root directory for temporary download staging; defaults to the system temporary directory.

The service enforces limits of 128 MB per attachment, 512 MB total downloaded data, 512 MB expanded ZIP data, five nested ZIP levels, and 5000 archive entries. Unsafe archive paths and symlink entries are skipped.

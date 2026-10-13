Owner: cs-dongqi@zepp.com
Organization: Active.Bu

# LTHR Jira Attachment Service

This small internal service retrieves Jira attachments for the browser-based LTHR analyzer. It does not parse logs or generate CSV files.

## Behavior

- `GET /health` reports whether the process is running.
- `POST /api/v1/issues/{issueKey}/attachments` downloads every Jira attachment for the issue.
- ZIP archives are expanded recursively before candidate files are selected.
- Matching log files are deduplicated by SHA-256 content, so an attached log and an identical copy extracted from a ZIP are only returned once. The original attachment is kept ahead of extracted copies.
- The service returns a JSON manifest for all files named `MILI*.log`, `MILI*.txt`, `ios*.log`, `ios*.txt`, and all `.fit` files.
- Each matched file has a temporary `downloadUrl` that the browser can fetch as data and add directly to the analyzer's file lists. This is an in-page fetch; it does not prompt the user to save a file.
- If no Jira attachments or no matching logs exist, the service returns JSON and marks feedback retrieval as `not_implemented`.
- Matched files are staged for 15 minutes so the browser can fetch them individually. Expired files are removed when the service next handles a session-file request or a new Jira search.

Matching is case-insensitive. A FIT file is included when found, but a FIT file alone does not count as a matching log.

## Configuration

Python 3.8+ and the standard `jira` CLI (`ankitpokhrel/jira-cli`) must be installed and available in `PATH`. The service checks for `jira` at startup and exits with an error if it is missing. It calls `jira issue view {issueKey} --raw` to read the issue and its attachment metadata. This CLI version has no attachment-download subcommand, so the service downloads each returned content URL using the same host and credentials from the Jira CLI config plus the local `.netrc` file. Jira CLI credentials remain local; no duplicate Jira token or service API key environment variables are required.

The current Jira CLI configuration file defaults to `~/.config/.jira/.config.yml`. Attachment downloads reuse credentials from `~/.netrc` with restrictive permissions (`chmod 600 ~/.netrc`), matching Jira CLI's local authentication setup. `JIRA_API_TOKEN` is also accepted if it is already set in the environment.

Optional environment variables:

- `JIRA_CLI`: explicit Jira CLI executable path; otherwise `jira` is searched in `PATH`.
- `JIRA_CONFIG_FILE`: Jira CLI config path; defaults to `~/.config/.jira/.config.yml`.
- `HOST`: defaults to `127.0.0.1`.
- `PORT`: defaults to `8080`.
- `ALLOWED_ORIGIN`: comma-separated exact browser origins to allow for CORS; defaults to `https://active-ailab.github.io`, `http://127.0.0.1:8000`, and `http://localhost:8000`.

Start from this directory with:

```bash
python3 app.py
```

The service is bound to localhost by default. Keep that terminal open while using it. Check it from another terminal with:

```bash
curl http://127.0.0.1:8080/health
```

The expected response is `{"status": "ok"}`.

### Local page test

Use two terminals. Start the Jira attachment service in the first:

```bash
cd lthr-jira-analyzer/server
python3 app.py
```

Start a static page server from the repository root in the second:

```bash
python3 -m http.server 8000
```

Open `http://127.0.0.1:8000/` in the browser. Do not open `index.html` as a `file://` URL; browsers assign those pages an opaque `null` origin, which cannot use the service's local CORS allowlist. The browser page calls the attachment service at `http://127.0.0.1:8080`.

The website will call this localhost service directly. Keep it bound to localhost; the service does not expose an API key because it is intended for local use.

## API

```http
POST /api/v1/issues/ITS-58513/attachments
```

On success, the response is JSON. It lists matched files with `fileId`, `type` (`log` or `fit`), original `name`, `size`, and `downloadUrl`. The browser fetches each URL and wraps the response body in a `File` object for the analyzer; no user download is needed. The individual-file endpoint is `GET /api/v1/sessions/{sessionId}/files/{fileId}`.

The service retains matched files for 15 minutes. The website should fetch them immediately after receiving the manifest, add them to the existing log/FIT lists, and leave analysis initiation to the user's **Start analysis** action.

No-attachment and no-log responses use JSON with HTTP 404. Both include `feedbackFallback: "not_implemented"` for the future fallback path.

The service enforces limits of 128 MB per downloaded attachment, 512 MB total download size, 512 MB expanded ZIP data, 5 nested ZIP levels, and 5000 archive entries. Unsafe archive paths and symlink entries are skipped.

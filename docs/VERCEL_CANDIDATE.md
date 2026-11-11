# Vercel candidate frontend with FastAPI on the same laptop

This setup deploys **static candidate assets only**. Chrome downloads the interface from Vercel and calls `http://127.0.0.1:8000` directly on the laptop where Chrome runs. Recruiter management, interview logic, providers and JSON records remain local. On another computer, loopback points to that other computer; this is not public backend hosting.

## What changed

The inspected repository used Jinja HTML and esbuild bundles in `app/static/`, with no standalone output. Existing token-authorized candidate APIs supply all required data, so no API routes were added.

The new build reuses the candidate template content, current handlers, CSS, worker, models and WASM. A static wrapper replaces the Jinja layout. Session identity comes from the URL; participation data comes from the existing authorized API. Unexpected Jinja expressions fail the build rather than leaking an incomplete template.

No Functions, API proxies, tunnels, new dependencies or alternate audio pipeline are introduced. The local build remains independent and same-origin.

## Exact Vercel settings

Import the repository and select the branch containing these changes.

| Setting | Value |
|---|---|
| Framework Preset | **Other** |
| Root Directory | Repository root: leave empty / use `.` |
| Install Command | `npm --prefix frontend ci` |
| Build Command | `npm --prefix frontend run build:static` |
| Output Directory | `frontend/dist` |
| Node.js Version | `22.x` |
| Public environment variable | `PUBLIC_API_BASE_URL=http://127.0.0.1:8000` |

Root `vercel.json` supplies install/build/output settings, `framework: null` (Other), security headers and a static rewrite from `/candidate/<session-id>` to `/index.html`. Keep the project root at the repository root: the build reads templates and styles under `app/`.

`PUBLIC_API_BASE_URL` is the one public build value consumed by our esbuild script. Its default is already `http://127.0.0.1:8000`; changing it requires a rebuild. Only HTTP loopback origins are accepted.

Do not upload `.env` or add Sarvam/Cerebras keys to Vercel. Do not select the FastAPI preset, deploy the repository root as the output, or enable candidate analytics/session replay. Setup downloads missing pinned public vision models from Google's hosting and checks their SHA-256 hashes. Runtime vision assets are served from Vercel itself.

References: [Vercel build configuration](https://vercel.com/docs/builds/configure-a-build), [vercel.json configuration](https://vercel.com/docs/project-configuration/vercel-json).

## Configure the actual Vercel origin locally

Vercel assigns the production hostname when you create the project. The repository name alone does not prove which hostname you own, so the backend does not guess one or trust `*.vercel.app`.

1. Deploy the static frontend and copy the production origin Vercel assigns, such as `https://your-assigned-project.vercel.app`.
2. Start local FastAPI using the command below.
3. Open `http://127.0.0.1:8000/config`.
4. Under **Ops**, set **Hosted candidate origin (optional)** to that exact HTTPS origin, without a path or trailing slash, and save.

The local behavior setting is `ops.candidate_frontend_origin` in `data/runtime_config.json`. Blank disables cross-origin access. Existing configuration files default to blank. This setting belongs in local JSON, not `.env`, and takes effect after saving.

Only the exact configured origin receives CORS access to `/api/candidate/…`, with GET, POST, PATCH and OPTIONS and Authorization/Content-Type headers. Cookies are not shared. Tokens and WebRTC peer ownership are still validated. Hosted requests require a loopback peer and local Host.

Recruiter routes, reports, settings, history and `/api/interviews` receive no hosted CORS permission. Unlisted origins, including preview URLs, are rejected. Clearing or replacing the setting revokes access.

## Start the local backend

On the same laptop as Chrome:

```powershell
cd "C:\Users\Naman\Downloads\RVU\VoiceAgent---Project-main"
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run prepare:vision
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Keep provider credentials in the existing local `.env`. Installation, preparation and build are setup commands; subsequent starts normally need only Uvicorn unless sources/dependencies changed. Keep one worker and loopback binding.

To build the standalone output locally:

```powershell
$env:PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
npm.cmd --prefix frontend run build:static
```

Only `frontend/dist/` is deployable. Its explicit file allowlist contains `index.html`, four candidate-related JavaScript bundles, three stylesheets, two detection models and four vision runtime files. An audit rejects unexpected/missing files and broken HTML asset references. No Python, recruiter bundle, credentials, transcripts, reports, interview records, source maps or Functions are copied. `.vercelignore` excludes private/local files from CLI uploads too.

## Convert a local candidate link

Create/review/activate an interview at `http://127.0.0.1:8000/interviews`. Keep the original link:

```text
http://127.0.0.1:8000/candidate/<session-id>#token=<participation-token>
```

Replace **only the origin**:

```text
https://your-assigned-project.vercel.app/candidate/<session-id>#token=<participation-token>
```

Open this URL in Chrome on the same laptop, then click **Connect to local backend**. Existing local links still work unchanged. Existing valid sessions can use the hosted UI; completed/abandoned sessions retain their terminal behavior.

The fragment is not sent to Vercel's HTTP server. The token is retained in tab-scoped session storage before the fragment is removed. Refresh in that tab retains it; a different tab needs the original complete link. If storage is denied, the fragment is retained for refresh. Never move the token into a query string. If the original one-time link was lost, use the existing copy/activation workflow to issue a fresh session/link.

Candidate tokens are sent only in Authorization headers to the configured local API. The frontend adds no analytics/token logging and uses no-referrer policies.

## Chrome permission and failure handling

The hosted UI waits for a Connect click before contacting loopback. Initial offers, later ICE updates, end/retry/page-exit, preparation and monitoring all use the configured API base and declare the loopback address space. The pinned SDK clones native Request objects and drops some network metadata. A hosted-only esbuild adapter restores it at the SDK's final fetch call, scoped to this candidate API; global fetch and the local build are unchanged. The build checks that both pinned SDK entry points were adapted.

Chrome can require local-network / apps-on-device permission for an HTTPS site to access the laptop backend. Allow it for the production site; if denied, restore it in that site's Chrome settings. Managed browser policies may require administrator action. Do not disable browser security. See [Chrome's Local Network Access guide](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access).

The generated CSP permits the configured HTTP API and does not contain `upgrade-insecure-requests`, which would change the loopback endpoint to HTTPS. The camera worker remains restricted to same-origin vision assets.

A failed local fetch displays instructions to start FastAPI on this device, allow Chrome network permission and check the exact configured Vercel origin. HTTP invalid-token/invalid-link messages remain distinct. Closing a tab is still best-effort cleanup: prefer explicit Pause or End and wait for saving.

## Chrome smoke test after deployment

1. Start FastAPI; save the exact Vercel production origin in local Configuration.
2. Create/activate an interview locally, convert its link and open it on Vercel.
3. Click Connect; allow Chrome network permission. Confirm title/language. Refresh and reconnect once to check token retention.
4. Check the microphone. Play the speaker tone and confirm you hear it.
5. Preview the camera; consent remains unchecked until you select it. Confirm voice-only participation is also available.
6. Prepare devices and Start. Hear the introduction/first question, answer after the interviewer stops, and hear one follow-up or next question. Confirm microphone gating during interviewer speech.
7. With monitoring consent, confirm analyzed-frame counts increase. Counts establish processing activity, not recognition accuracy.
8. Try Pause/Resume, then End interview permanently and confirm. Wait for cleanup and check that tracks stop. Review the saved report on the local recruiter page.
9. Separately stop FastAPI and reconnect to verify the backend/permission guidance; restart it afterward.

Step 6 uses provider APIs and may be billable. Run it manually with your own normal credentials.

## Verification boundaries

Automated tests cover local/hosted URLs, refresh token retention, Request-based POST/PATCH signaling, end/keepalive wiring, CORS and token enforcement, local recruiter restrictions, configuration validation and static output auditing.

Browser checks use an isolated local HTTPS static server with the candidate rewrite, real loopback FastAPI routes with synthetic records, and fake devices. They do not publish to Vercel, prove real public-origin Chrome permission behavior, call paid providers or complete a real media interview. Actual hosting, microphone/speaker behavior, latency and voice completion require the smoke test above.

## Results for this implementation

- Existing local build and new static build passed. Final static output targets `http://127.0.0.1:8000`; it contains exactly 14 audited public files.
- Frontend: **81 tests passed**, including local same-origin compatibility and hosted configuration/signaling cases.
- Backend: **491 tests passed**, **3 live tests deselected**. Existing dependency deprecation warnings remain.
- Headless Chrome passed direct opening, refresh/token retention, real cross-origin FastAPI authorization/preparation, actual bundled SDK POST/PATCH calls against a mocked media manager, fake microphone/camera preparation, both local vision model/WASM initializations, invalid-token behavior and unreachable-backend guidance. No runtime exceptions or external requests occurred.
- No paid provider requests, real audio conversation or Vercel publication were performed.

Key implementation files are `frontend/build-static.mjs`, `frontend/candidate-shell.html`, `frontend/candidate-config.js`, the hosted-only `frontend/sdk-loopback-plugin.mjs`, `app/services/candidate_cors.py`, the optional runtime Ops setting, and root `vercel.json`. Existing media/controller modules retain their ownership and behavior.

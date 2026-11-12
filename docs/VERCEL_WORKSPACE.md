# Full Vercel frontend with FastAPI on the same laptop

Vercel hosts the interface and public vision assets. Chrome calls FastAPI directly
at `http://127.0.0.1:8000` on the same laptop. Provider keys, interview processing,
transcripts, reports and JSON records remain local. Another computer's loopback
points to that computer, so this setup does not provide remote backend access.

The hosted homepage now opens the recruiter dashboard. Candidate pages still need
their own scoped participation links. See the [approved scope](VERCEL_WORKSPACE_PLAN.md).

## Exact deployment settings

| Setting | Value |
|---|---|
| Application / Framework Preset | Other |
| Root Directory | Repository root (`./`) |
| Install Command | `npm --prefix frontend ci` |
| Build Command | `npm --prefix frontend run build:static` |
| Output Directory | `frontend/dist` |
| Node.js Version | 22.x |
| Public environment variable | `PUBLIC_API_BASE_URL=http://127.0.0.1:8000` |

Push to the connected production branch (`main` for this project) and let Vercel
rebuild. Root `vercel.json` supplies static rewrites and security headers. Keep the
root at the repository root because the build reads existing templates and styles.
Do not add provider keys to Vercel or enable analytics/session replay for these pages.
No Functions, API proxies or tunnels are used. Face and phone detection share the
pinned MediaPipe runtime; ONNX Runtime was removed when the YOLO trial was retired.

Only `frontend/dist` is served: six data-free HTML shells, six JS bundles, three
stylesheets, two vision models and four vision runtime files (**21 files**). The
build audits an explicit allowlist and HTML asset paths. Backend code, `.env`,
real records, transcripts, reports and source maps are excluded.

## Start FastAPI and connect (no pairing code)

After updating the code, build the local assets once:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Later starts normally need only Uvicorn. Keep the existing local `.env`, one worker
and the loopback binding.

1. Open `http://127.0.0.1:8000/config` on the same laptop as Chrome.
2. Under Ops, set **Hosted candidate origin** and **Hosted workspace origin** to
   `https://voxlaneai.vercel.app` (the current production origin), or your
   actual assigned production origin if you change domains. No trailing slash. Save.
3. Open the Vercel homepage. It connects to the local backend automatically, with no
   connection panel, code or button needed on a successful visit. Navigation and
   refresh each attempt connection automatically, including a fresh tab.
4. If the backend is unavailable or Chrome needs local-network/apps-on-device
   permission, a compact message and **Retry connection** appear. Start FastAPI,
   allow the browser permission and retry. Chrome still controls permission; the
   application does not bypass it. Successful connection hides the message again.

Both origin settings default blank, so hosted access requires explicit local approval.
Candidate-origin approval alone does not enable management. These are local JSON
settings, not environment variables. Hosted settings cannot change either approval.
To revoke hosted management access, clear **Hosted workspace origin** locally and save.
The change applies immediately. Restarting FastAPI does not require a new code.

Old recruiter pairing routes, controls and credentials have been removed. The new
client removes the obsolete `workspace-pairing-...` entry from tab storage. Candidate
participation tokens are unchanged and still protect candidate session APIs.

## Pages and interview links

| Hosted path | Interface |
|---|---|
| `/` or `/interviews` | Recruiter builder, reviewed plans and reports |
| `/interviews?report=<session_id>` | Separate recruiter report tab, using the same page and authorized APIs |
| `/talk` | Existing open-ended voice demo |
| `/config` | Runtime behavior settings |
| `/calls` | Local saved conversation history |
| `/calls/<call_sid>` | Transcript and turn metrics |
| `/candidate/<session_id>#token=...` | Scoped candidate interview |

Create, review/save and activate an interview on the hosted dashboard. Its relative
candidate URL automatically resolves on the hosted origin. Save the full link
before leaving. Open it on this same laptop without a recruiter credential.
The candidate page automatically loads its authorized interview data when FastAPI
is available. It shows no connection panel or startup instructions on success.
Only a failed load exposes the error and a compact Retry connection button. Device
checks, camera consent, preparation and Start interview remain explicit user actions;
opening the link does not request microphone/camera access or start provider work.

Local links remain local and still work. To open one on Vercel, replace only
`http://127.0.0.1:8000` with your Vercel origin, keeping the entire
`/candidate/<session_id>#token=...` suffix. The fragment token is retained in tab
storage for refresh; another tab needs the original complete link. Never move it
to a query string. Candidate pages retain device checks, consent, local camera
processing, voice gating, timing, follow-ups, pause/resume and cleanup.

The original local pages keep working: recruiter `/interviews`, Talk `/`, settings
`/config`, history `/calls`, and candidate links. Hosted navigation uses `/talk`.

## Access and implementation

Existing recruiter JSON routes retain their paths. Additional data routes are
`GET`/`PUT /api/workspace/config`, `GET /api/workspace/calls` and
`GET /api/workspace/calls/{call_sid}`. Configuration contains behavior only, never
provider keys. History reuses existing storage and timing calculations. Interview
record shapes are unchanged. The old `/api/workspace/pair` routes no longer exist.

Cross-origin recruiter access requires a loopback peer, loopback Host and the exact
locally approved workspace origin. That origin is the browser access boundary: pages
served from it can call the listed recruiter APIs on this laptop without a separate
login. CORS permits GET/POST/PUT/PATCH/OPTIONS and Content-Type, with no cross-origin
cookies or recruiter Authorization header. Unlisted origins, management HTML and
unlisted APIs remain blocked. No public backend listener or tunnel is introduced.

Candidate CORS stays separate and retains its per-session bearer validation. Candidate
origin approval and a participation token do not enable recruiter APIs unless workspace
origin access is also explicitly approved locally. Clearing workspace approval does
not revoke candidate participation.

Static shells reuse the current templates, CSS, interview/report handlers, worker
and models. Settings controls are generated without data and hydrated through the
origin-restricted API. History renders transcript strings as text. The hosted SDK fetch adapter
handles candidate and general Talk POST/PATCH offers; end/retry/page-exit calls use
the same loopback base and network/referrer options. Local builds stay same-origin.
Audio remains fixed at 16000 Hz; providers, observers and session ownership are unchanged.

## Chrome smoke test

1. Start FastAPI, set both origins locally and open the hosted homepage. Confirm
   automatic loading with no connection panel, then refresh and open a fresh tab.
2. Save an ordinary setting on hosted Configuration. Confirm origins are disabled
   there and still managed locally; restore your test setting if needed.
3. Create/review/activate an interview on Vercel. Open its candidate link and confirm
   it loads automatically without a connection panel. Refresh to check token retention.
4. Allow Chrome network/microphone/camera access. Run microphone/speaker checks,
   preview the camera, choose consent, prepare and start. Hear one question, answer
   after it stops, and hear the next response. Check mic gating during bot speech.
5. Pause/resume and check the preview returns. End permanently, wait for saving and
   read the report on the hosted recruiter page. Confirm records remain local.
6. Separately run Talk and End; follow its hosted history link. Only one session runs
   at once. Review transcript and timing summaries.
7. Clear Hosted workspace origin locally, refresh the hosted list and confirm access
   is denied. Stop FastAPI and retry to check the same-laptop/network guidance.

Live speech steps may incur provider charges. Automated checks use synthetic records,
fake devices and mocked media/provider boundaries. They do not prove audible speech,
real latency, detector accuracy or the public Vercel network-permission prompt.

References: [Vercel build configuration](https://vercel.com/docs/builds/configure-a-build),
[Chrome Local Network Access](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access).

## Talk initialization fix

Talk previously skipped the audio-helper preload already used by the candidate page.
The Start action now creates/reuses Daily's existing device helper and loads it with
`dailyConfig.avoidEval=true` before constructing SmallWebRTC. The strict CSP remains
in place. This eliminated the reproduced generic startup error in the isolated Chrome
check. SDK/device failures now retain `errorMsg`, HTTP details and distinct microphone
permission/device guidance instead of an empty generic connection message.

Automated checks use fake devices and a local synthetic WebRTC/RTVI responder, not
paid speech providers. Real voice, microphone hardware and latency still need the
manual Chrome test above.

## Verification for this revision

- **91 frontend tests** and **502 backend tests** passed; three live tests were
  deselected. Both builds pass, and the final deployment output is audited against
  its 21-file public allowlist with the API restored to port 8000.
- Headless Chrome passed no-code connection, direct page navigation and refresh,
  interview creation/review/activation, hosted candidate links, reports, settings,
  history/detail, candidate authorization, static vision model/WASM loading and
  origin revocation. No runtime errors or paid provider calls occurred.
- A separate test reproduced the old generic Talk error, then passed the actual
  Start button, fake microphone initialization, local WebRTC/data-channel setup,
  synthetic RTVI readiness, microphone gating, End and history-link behavior after
  the helper preload fix. The responder sent silent test audio, not synthesized speech.
- No `.env` or private interview records were changed, and these changes were not
  pushed or published automatically.

The automatic-connection revision removes the workspace connection panel and adds
bootstrap checks for a fresh visit, offline retry and preserving existing page/media
controls during reconnect. All 94 frontend tests and both builds pass. Isolated
Chrome checks passed automatic first loading, navigation to all workspace pages,
refresh, offline guidance and retry recovery, with no provider calls. Existing local
pages and candidate signaling are unchanged.

The candidate automatic-loading revision also removes its connection panel. All 97
frontend tests pass, including automatic hosted loading, failed-load retry, unchanged
invalid-token handling and no automatic device access or voice start. Both builds and
two template-rendering tests pass. Isolated Chrome checks also passed automatic
candidate loading with no startup block, refresh/token retention, offline retry and
invalid-token errors, with zero device requests, runtime errors or provider calls.

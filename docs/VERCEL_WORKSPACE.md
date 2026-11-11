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
No Functions, API proxies, tunnels or additional dependencies are used.

Only `frontend/dist` is served: six data-free HTML shells, six JS bundles, three
stylesheets, two vision models and four vision runtime files (**21 files**). The
build audits an explicit allowlist and HTML asset paths. Backend code, `.env`,
pairing codes, real records, transcripts, reports and source maps are excluded.

## Start FastAPI and pair the hosted dashboard

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
   the exact production URL Vercel assigns, such as `https://your-project.vercel.app`.
   Include no path or trailing slash. Save.
3. Below the form, click **Create pairing code**, then **Copy code**.
4. Open the Vercel homepage, paste the code into **Recruiter pairing code**, then
   click **Connect to local backend**. Allow Chrome local-network/apps-on-device
   permission if prompted.
5. The dashboard loads local interviews. Navigation and refresh retain the code in
   that tab. A different tab may need the code again.

The new `ops.workspace_frontend_origin` defaults blank. Candidate-origin approval
alone never authorizes management. Both origins are local JSON access settings,
not environment variables. Hosted settings cannot change these local approvals.

Pairing expires after **two hours**. Creating a replacement code, **Revoke hosted
access**, changing/clearing the workspace origin or restarting FastAPI invalidates
the previous code. Re-pair locally when needed. **Disconnect this tab** removes its
stored credential and reloads; use local revoke to invalidate the code server-side.

The backend retains only an in-memory token hash, bound to the approved origin.
The hosted client stores its code in tab-scoped session storage and sends it only
in Authorization headers to loopback, never in URLs, logs or deployment output.
Storage denial still permits the current page but requires pairing after navigation.

## Pages and interview links

| Hosted path | Interface |
|---|---|
| `/` or `/interviews` | Recruiter builder, reviewed plans and reports |
| `/talk` | Existing open-ended voice demo |
| `/config` | Runtime behavior settings |
| `/calls` | Local saved conversation history |
| `/calls/<call_sid>` | Transcript and turn metrics |
| `/candidate/<session_id>#token=...` | Scoped candidate interview |

Create, review/save and activate an interview on the hosted dashboard. Its relative
candidate URL automatically resolves on the hosted origin. Save the full link
before leaving. Open it on this same laptop without the recruiter pairing code.

Local links remain local and still work. To open one on Vercel, replace only
`http://127.0.0.1:8000` with your Vercel origin, keeping the entire
`/candidate/<session_id>#token=...` suffix. The fragment token is retained in tab
storage for refresh; another tab needs the original complete link. Never move it
to a query string. Candidate pages retain device checks, consent, local camera
processing, voice gating, timing, follow-ups, pause/resume and cleanup.

The original local pages keep working: recruiter `/interviews`, Talk `/`, settings
`/config`, history `/calls`, and candidate links. Hosted navigation uses `/talk`.

## Access and implementation

Existing recruiter JSON routes retain their paths. Approved additions are local-only
`POST`/`DELETE /api/workspace/pair`, `GET`/`PUT /api/workspace/config`, and
`GET /api/workspace/calls` plus `GET /api/workspace/calls/{call_sid}`. Configuration
contains behavior only, with no provider credentials. History handlers reuse existing
storage and timing calculations. Interview persistence shapes are unchanged.

Cross-origin recruiter access requires a loopback peer, loopback Host, the exact saved
workspace origin and a valid temporary paired credential. CORS is limited to listed
workspace/interview/voice APIs: GET/POST/PUT/PATCH/OPTIONS with Authorization and
Content-Type, no cross-origin cookies. Management HTML, unlisted APIs and pairing
issuance/revocation remain inaccessible cross-origin. Approval changes act immediately.

Candidate CORS stays separate with its existing methods and per-session bearer
validation. Candidate tokens cannot read recruiter data; pairing tokens cannot serve
as candidate tokens. Revoking pairing does not revoke candidate participation.

Static shells reuse the current templates, CSS, interview/report handlers, worker
and models. Settings controls are generated without data and hydrated through the
paired API. History renders transcript strings as text. The hosted SDK fetch adapter
handles candidate and general Talk POST/PATCH offers; end/retry/page-exit calls use
the same loopback base and network/referrer options. Local builds stay same-origin.
Audio remains fixed at 16000 Hz; providers, observers and session ownership are unchanged.

## Chrome smoke test

1. Start FastAPI, set both origins locally, pair at the hosted homepage and refresh.
2. Save an ordinary setting on hosted Configuration. Confirm origins are disabled
   there and still managed locally; restore your test setting if needed.
3. Create/review/activate an interview on Vercel. Open its candidate link and Connect.
4. Allow Chrome network/microphone/camera access. Run microphone/speaker checks,
   preview the camera, choose consent, prepare and start. Hear one question, answer
   after it stops, and hear the next response. Check mic gating during bot speech.
5. Pause/resume and check the preview returns. End permanently, wait for saving and
   read the report on the hosted recruiter page. Confirm records remain local.
6. Separately run Talk and End; follow its hosted history link. Only one session runs
   at once. Review transcript and timing summaries.
7. Revoke pairing locally, refresh the hosted interview list and confirm re-pairing
   is required. Stop FastAPI and retry to check the same-laptop/network guidance.

Live speech steps may incur provider charges. Automated checks use synthetic records,
fake devices and mocked media/provider boundaries. They do not prove audible speech,
real latency, detector accuracy or the public Vercel network-permission prompt.

References: [Vercel build configuration](https://vercel.com/docs/builds/configure-a-build),
[Chrome Local Network Access](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access).

## Verification for this change

- Both the existing local build and full static build pass; the final output targets
  `http://127.0.0.1:8000` and contains 21 audited public files.
- All **87 frontend tests** pass. The backend suite passed **501 tests**, with three
  live tests deselected. After adding protection against concurrent local origin
  changes being overwritten, **53 relevant backend tests** passed, including the new
  concurrency regression and config/storage/access checks.
- Headless Chrome passed the recruiter root, paired connection, existing builder
  creation/review/activation, hosted candidate link, report loading, settings save,
  refresh, history/detail, text-safe transcript, actual bundled SDK POST/PATCH against
  a mocked media manager, end request, candidate authorization, fake mic/camera,
  face/phone model and WASM loading, and paired access revocation.
- Browser checks used isolated HTTPS/loopback fixture ports and synthetic records,
  with zero runtime errors, external requests or paid provider calls. They do not
  replace the real Vercel/Chrome media smoke test. These changes were not pushed or
  deployed automatically.

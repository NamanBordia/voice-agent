# Full hosted frontend with a loopback backend — proposed scope

Status: original scope approved on 2026-11-11; subsequently amended at the user's
explicit request to remove recruiter pairing codes. The pairing sections below record
the original design and no longer describe current behavior. Current code uses exact
origin plus loopback access. Setup is in [VERCEL_WORKSPACE.md](VERCEL_WORKSPACE.md).

## Why the deployed root opens the candidate page

The first deployment followed the candidate-only requirement: the static build writes
the candidate shell to `frontend/dist/index.html`. Opening `/` therefore displays that
shell without a session ID or participation token. Its incomplete-link message is
expected. Recruiter pages were intentionally excluded, and management CORS is blocked.

## Revised outcome

Host the complete existing interface on Vercel while FastAPI, provider credentials,
interview control, transcripts, reports and JSON storage remain on the same laptop.
The hosted root opens the recruiter interview workspace. Existing local pages keep
working. A hosted candidate page still requires its own scoped participation link.

Hosted frontend paths:

| Path | Interface |
|---|---|
| `/` and `/interviews` | Recruiter workspace, interview builder and reports |
| `/talk` | Existing open-ended voice conversation |
| `/config` | Existing behavior settings |
| `/calls` | Conversation history |
| `/calls/<call_sid>` | Saved conversation detail and metrics |
| `/candidate/<session_id>#token=...` | Candidate interview |

The local Talk page remains at `/`. Hosted navigation explicitly points to `/talk`.
Static shells reuse the current styling, controls and frontend tools. Dynamic local
data loads after connection; it is never embedded in deployment output. Existing
recruiter handlers and readable report rendering remain the source of behavior.

## Access boundary

Candidate-origin approval continues to authorize only candidate participation APIs.
It must not silently grant access to recruiter records or settings.

Add an optional `ops.workspace_frontend_origin` field to runtime JSON, defaulting to
blank and validated as one exact HTTPS origin. Enable it from the local Configuration
page. Its value is independent of `ops.candidate_frontend_origin`.

Provide an explicit pairing action on the local workspace. It generates a temporary
recruiter bearer credential held only in backend memory. The user copies it into the
hosted workspace connection panel. Store it only in tab-scoped session storage; send
it in Authorization headers, never URLs, logs or analytics. A backend restart or
replacement pairing revokes it. Include expiry and an explicit local revoke action.
Candidate participation credentials cannot authorize management.

Hosted recruiter requests require all of: loopback peer, loopback Host, the exact
configured workspace origin, and a valid paired recruiter credential. Candidate
preflight and token checks stay intact. Local same-origin recruiter access continues
to work. Foreign origins and unpaired hosted users remain blocked. Hosted settings
must not change origin approvals or pairing controls; those stay local.

## Proposed route additions

Existing recruiter JSON APIs already cover interview drafting, review, activation,
reports and human notes. Preserve their paths and add paired hosted authorization.
Existing general voice offer/PATCH/end routes need the same hosted authorization
and loopback fetch handling used by the candidate SDK.

The missing data and pairing surfaces are:

| Route | Responsibility |
|---|---|
| `POST /api/workspace/pair` | Create/replace pairing; local same-origin only |
| `DELETE /api/workspace/pair` | Revoke pairing; local same-origin only |
| `GET /api/workspace/config` | Validated non-secret runtime settings |
| `PUT /api/workspace/config` | Save validated settings, excluding local access controls |
| `GET /api/workspace/calls` | Existing history list and computed summaries as JSON |
| `GET /api/workspace/calls/{call_sid}` | Existing conversation detail and metrics as JSON |

Keep endpoint handlers thin and reuse existing config storage, history calculations
and interview services. No changes to interview record shapes, audio sample rates,
observers, provider behavior, or session ownership. No new dependencies, workers,
proxies, tunnels or Vercel Functions.

## Build, links and checks

Extend the standalone build with workspace assets and route rewrites. Retain the
existing local build. Keep `PUBLIC_API_BASE_URL=http://127.0.0.1:8000`, repository-root
builds and `frontend/dist` output. Candidate links created by the hosted recruiter
resolve on the hosted origin automatically; local recruiter links remain local.

Expand output auditing to an explicit public-asset allowlist. Keep backend code,
credentials, pairing tokens, transcripts, reports and real records out of output.

Verify both builds, direct navigation and refresh for all hosted paths, pairing and
revocation, exact-origin CORS, candidate/recruiter separation, config validation,
history/report loading, generated candidate links, and POST/PATCH/end signaling
without paid provider calls. A human Chrome smoke test must validate actual voice,
camera and public-origin local-network permissions on the same laptop.

## Repository approval requirement

`AGENTS.md`, **Ask First**: “Changing public route structure, the persistence shape,
or observer registration.” This proposal adds routes and one backward-compatible
runtime configuration field. Full workspace hosting also replaces the earlier
candidate-only access boundary. Approval covers the scoped changes above.

# Vercel candidate frontend

The initial candidate-only deployment has been expanded, with user approval, into
the full hosted workspace. Current settings, local origin approval, access boundaries and
Chrome smoke tests are in [VERCEL_WORKSPACE.md](VERCEL_WORKSPACE.md).

The hosted root now opens the recruiter dashboard. Candidates still open
`/candidate/<session_id>#token=<participation_token>`, authorize through the existing
candidate APIs and process camera frames locally. Candidate tokens remain required.

Locally created candidate links still work. Replace only their loopback origin
with the production Vercel origin to use the hosted frontend. Set **Hosted candidate
origin** in local Configuration. The separate **Hosted workspace origin** setting
controls recruiter access without a pairing code.

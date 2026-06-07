# Security Policy

Hermes Phone is a phone server: it answers real calls, can place outbound calls that cost
money, and stores voicemail audio + transcripts. Treat it like any internet-facing service.

## Threat model

Twilio reaches the server over the public internet, so the webhook routes (`/voice/*`,
`/ws/call`) **must** be publicly reachable. Everything else (the dashboard, `/call`,
voicemail listing/audio, settings, exports, `/health`) is a private control plane and must
not be exposed unauthenticated.

## How the server protects itself

| Surface | Protection |
|---|---|
| `/voice/*` webhooks | Twilio request-signature validation (`X-Twilio-Signature`). Requires the correct `TWILIO_AUTH_TOKEN` and a matching `PUBLIC_URL`. |
| Dashboard / API / `/call` / exports / `/health` | Trusts **direct localhost** only. Remote browsers sign in at `/login` (token → HttpOnly, Secure, SameSite=Strict cookie); API clients send `X-Hermes-Token` / `Authorization: Bearer`. Tokens are never accepted in query strings. |
| PIN gate | Per-caller rate limiting + lockout (`PIN_MAX_ATTEMPTS`, `PIN_LOCKOUT_WINDOW`), constant-time compare. |
| Settings writes | Values are sanitised before being written to `.env` (no newline/quote injection). |
| Debugger | `debug` is **off** by default; only enable `HERMES_DEBUG=true` on a trusted localhost dev box. |
| Secrets at rest | The installer writes `.env` with `chmod 600`. |

## Deployment guidance

- **Never** forward port 5050 to the internet directly. Put a TLS tunnel/reverse proxy in
  front and, ideally, only route `/voice/*` and `/ws/call` publicly.
- Set `PUBLIC_URL` to your real https origin so signature validation and `wss://` Media
  Streams work.
- Keep `HERMES_API_TOKEN` secret. For remote dashboard access, open `https://<host>/` and
  sign in; the token is stored as an HttpOnly cookie and never appears in a URL.
- Browser sessions are server-side and revocable: visit `/logout` to end the current
  session, or restart the server to invalidate **all** sessions.
- Rotate the token by editing `~/.hermes-phone/.env` and restarting the server.
- Use a non-default `VOICEMAIL_PIN` of reasonable length.

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue:

- Open a GitHub **security advisory** (Security → Report a vulnerability), or
- Email the maintainer listed on the repository.

Include reproduction steps and the affected version/commit. We aim to acknowledge within a
few days.

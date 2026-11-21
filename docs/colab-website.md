# Safestral on Colab: fixed domain and mandatory login

`notebooks/colab_website.ipynb` serves the React frontend and FastAPI/Sentinel-Loop
backend together at `https://safestral.dpdns.org` through a Cloudflare named tunnel.
The hostname remains stable when the Colab runtime restarts. Application
availability still depends on the Colab runtime being online.

## One-time domain and tunnel setup

1. Register `safestral.dpdns.org` through DigitalPlat if it is available. The
   free slot requires account verification. Check the displayed cost is $0.
2. Add the complete name to Cloudflare using its Free DNS plan. For the zone
   prepared in this chat, assign `eric.ns.cloudflare.com` and
   `raegan.ns.cloudflare.com` as the external nameservers in DigitalPlat.
   Wait for Cloudflare to report the zone as Active.
3. In Cloudflare, open Networking → Tunnels and create `safestral-colab`.
4. Add a Published application route with Subdomain and Path empty, Domain
   `safestral.dpdns.org`, protocol HTTP and service URL `localhost:8000`.
   Let Cloudflare create the tunnel DNS record; do not point a DNS A record at
   a Colab IP or retain a CNAME to an old random quick-tunnel hostname.
5. Copy the connector token from the tunnel setup command. This is the token
   for this tunnel, not a Cloudflare Global API Key.
6. In Colab Secrets (key icon), add `CF_TUNNEL_TOKEN` and
   `SENTINEL_WEB_PASSWORD`. Choose a unique random password of at least
   32 characters and enable notebook access for both secrets.

## Run the updated notebook

1. Close old cloudflared connectors using the first code cell **in the runtime
   that served the old public URL**. This stops all cloudflared processes in
   that runtime. If you cannot reconnect, use Colab's session manager to
   terminate that old session. Editing a saved notebook does not change code
   already running in an older runtime.
2. On the first upgrade from the threaded server, restart that session once.
   Data and model artifacts already saved to Drive remain there.
3. Run cells top to bottom. The project is read from
   `MyDrive/Colab Notebooks/Mistraton`; the updated frontend source is copied
   to local Colab storage and rebuilt each time.
4. Startup launches a fresh backend process on `127.0.0.1:8000` and verifies
   anonymous requests receive 401 while an authenticated health request works.
   An unknown process already listening on port 8000 stops startup.
5. The named-tunnel cell rechecks local authentication before connecting. It
   uses `CF_TUNNEL_TOKEN` from Secrets and has no quick-tunnel fallback. It
   then checks anonymous public requests, and closes its connector if a
   public authentication check fails or the hostname is not ready.
6. Open `https://safestral.dpdns.org`. The browser asks for username
   `safestral` (editable as `WEB_USERNAME`) and your chosen password.

## Authentication coverage

`backend/colab_security.py` is an ASGI guard around the whole Colab app.
It protects the homepage, assets, all API methods, uploads, pipeline jobs,
documentation, unknown routes and WebSocket upgrades. There is no public
health endpoint or implicit trusted-loopback bypass. Authenticated browser
writes must be same-origin, and no wildcard CORS policy is enabled. Public
HTTP requests redirect to HTTPS before a password challenge.

`SENTINEL_WEB_PASSWORD` is mandatory; a missing/short password stops app
construction. The legacy optional `SENTINEL_API_KEY` does not authenticate
requests, and keys are no longer injected into HTML or JavaScript. API
clients use HTTP Basic authentication on every request. Responses are
marked private/no-store. Treat this as one shared demo login; it is not
an individual-account system. Share the login only with authorized users.

Notebook probes send the password only to the local loopback backend.
Public probes send no credentials and do not follow redirects. Additional
Cloudflare Access authentication can be added, but its login redirects must
be accounted for in the public probes. The backend guard stays required.

For local use, set `SENTINEL_WEB_USERNAME`, `SENTINEL_WEB_PASSWORD` and,
when testing the public hostname, `SENTINEL_PUBLIC_URL` in your server's
environment before running `python colab_app.py --host 127.0.0.1 --port 8000`.
Never put actual credentials in the repository or notebook output.

## Validation

Run `PYTHONPATH=backend python -m pytest backend/tests/test_colab_app.py`.
The suite covers missing/wrong/malformed credentials, reads and writes,
static assets, a valid authenticated browser flow, cross-site requests,
forged proxy headers, HTTP-to-HTTPS behavior and WebSocket upgrades.

## Sources

- [Cloudflare named tunnel setup](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)
- [Cloudflare tunnel token](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/#token)
- [DigitalPlat registration](https://github.com/DigitalPlatDev/FreeDomain/tree/main/documents/tutorial/platform)
- [FastAPI HTTP Basic authentication](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/)

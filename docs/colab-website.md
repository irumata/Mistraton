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
6. In the project `Mistraton/.env`, set `CF_TUNNEL_TOKEN` to the Cloudflare
   connector token and `SENTINEL_WEB_PASSWORD` to a unique random password
   you create yourself (at least 32 characters). The notebook already loads
   this file. Colab Secrets are an optional fallback for settings absent from
   `.env`; enable notebook access if using that fallback.

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
   uses `CF_TUNNEL_TOKEN` from the project `.env` (or optional Colab Secrets) and has no quick-tunnel fallback. It
   then checks anonymous public requests, and closes its connector if a
   public authentication check fails or the hostname is not ready.
6. Open `https://safestral.dpdns.org`. The browser asks for username
   `safestral` (editable as `WEB_USERNAME`) and your chosen password.

## Walk through an investigation

1. On the homepage, select **Try a sample replay** to see the workflow using
   synthetic gateway metrics. Select **View monitor and decisions** to see the
   metric sources and rule, then **Replay observations** to watch the two candidate episodes
   appear. The sample runs in the browser without model credentials.
2. For your own incident, select a GPU runtime for Shieldstral in Colab. Set
   `MISTRAL_API_KEY` and `HUGGINGFACE_TOKEN` in the project `.env`. The Hugging
   Face token needs read access to `mistralai/Shieldstral-1.0-3B`. Run the
   notebook from the top to install the models and rebuild the site.
3. Select **Upload an incident** and choose a ZIP, timestamped CSV, access log,
   or text report. Use **Add supporting data or context** for up to eight extra
   files and an optional question. CSVs can use `timestamp,value` or separate
   columns for multiple metrics. ISO 8601 timestamps can include fractional
   seconds. If a download contains separate incident ZIPs, extract it and
   choose one incident ZIP. The limit is 20 MB combined and 64 MB unpacked.
   Set **Shieldstral candidate threshold** if you want a cutoff other than the
   default of 0.1, then select **Build monitor**.
4. **Metrics found** lists the source file and field for each series and marks
   Le Chonk’s choice. **Shieldstral baseline check** shows whether the early
   evidence was accepted and how that affected the Timestral band. Under
   **Episode decisions**, compare the measured departure, Shieldstral’s yes/no
   verdict, and Mistral Small 4’s assessment of episodes that pass. Shieldstral
   scores all detected episodes in batches. Small 4 considers plausible
   explanations and suggests a check that could distinguish
   them. Each detected episode stays visible with the checks it reached. If the evidence
   has only a report, the run can recommend instrumentation. A detector
   requires at least 16 populated time intervals for the selected metric.
5. In a completed replay with scored episodes, edit **Shieldstral candidate
   threshold** and select **Apply threshold**. The replay reuses the saved
   scores and reviews. Newly passing episodes are sent to Mistral Small 4.
   Baseline calibration uses its own 0.5 cutoff.
6. **Evidence used** shows the files and upload fingerprint. Each browser tab
   has independent access to its run, including after a reload. Results
   expire within an hour and when the Colab runtime restarts.

For a model-backed sample, download `/sentinel/investigations/sample` from the
authenticated site and use the ZIP as the incident file. It contains the public
nginx sample in `work/` and its timestamped metric export.

Select **Analyze Hugging Face’s July timeline** under **Build a monitor** to
review five published UTC daily totals from the July 2026 intrusion and get a
Large 4 monitoring plan. The first and last days are partial. The run displays
the original counts and skips forecasting. The CSV lives in
`work/public/hugging-face-july-2026/`.

Timestral 5M is the default. Set `SENTINEL_TIMESTRAL_SIZE=28M` before starting
the notebook to use 28M. Each run calibrates separately with shared model
weights. The first run downloads the model weights when the cache is empty.
Large 4 receives the investigation question, measured summaries, and limited
redacted excerpts of supporting text. Shieldstral and Timestral run in Colab.

## Try the incident voice demo

1. Set `MISTRAL_API_KEY` in the project `.env` and run the Colab website notebook.
   The voice demo uses the hosted Voxtral and Large 4 APIs, so the published
   timeline demo also works on a CPU runtime.
2. On the homepage, select **Try the voice demo** and then **Start voice briefing**.
   Safestral reads a briefing based on Hugging Face’s published July 2026
   incident timeline. If your browser blocks automatic audio, select **Play**
   on the player.
3. Select **Ask with microphone**, allow browser microphone access, ask a question,
   then select **Stop and send**. Try “Which day had the most recovered actions?”
   or “Can we use this data to test early warning?” Recordings stop after 25
   seconds. The typed input works without microphone access.
4. For your own upload, select **Open this incident’s voice briefing** from
   the investigation results. The briefing uses that run’s evidence, redacted
   excerpts, and available monitoring results. If the run expires, build the
   monitor again.

Voxtral transcribes the recording and reads Large 4’s answer aloud. Safestral
sends recordings and questions to Mistral for processing. The page shows the
briefing, each transcription, and each answer in text, even if playback is
unavailable. Microphone access requires the HTTPS site or a localhost preview.

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

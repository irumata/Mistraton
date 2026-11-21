# Run the whole website on Google Colab

`notebooks/colab_website.ipynb` runs the **entire app in one notebook**: the React
frontend, the FastAPI backend, the Sentinel-Loop pipeline UI (upload → ingest →
extract-metrics → label-windows → build-dataset → train → evaluate → finetune),
and a fine-tuned self-hosted Timestral checkpoint — exposed to the internet via a
cloudflared quick tunnel.

## Architecture

```
browser ──https──▶ https://<random>.trycloudflare.com ──▶ Colab :8000
                                                              │
              ┌──────────────────────── FastAPI (backend/colab_app.py) ───────────────────────┐
              │  /            built React app (frontend/dist, SPA fallback)                   │
              │  /api/*       status card endpoints (/api/db = SQLite check on Colab)         │
              │  /sentinel/*  shared watcher router (health, watch, feedback, alerts)         │
              │  /sentinel/upload, /sentinel/data, /sentinel/eval                              │
              │  /sentinel/pipeline/<stage> + /sentinel/pipeline/jobs  (background CLI jobs)  │
              │  /sentinel/timestral/chat   fine-tuned checkpoint on the Colab GPU            │
              └───────────────────────────────────────────────────────────────────────────────┘
                                                              │
              Drive: My Drive/Colab Notebooks/Mistraton/  (data/, .env, repo copy)
```

One origin → no CORS. The VM deployment (`backend/main.py` behind Caddy) is
unchanged; `colab_app.py` only **adds** routes on top of the shared
`create_sentinel_router()`.

## Run

1. Upload `notebooks/colab_website.ipynb` to Colab, Runtime → **GPU** (T4/A100) if
   you want `finetune-timestral`; CPU is fine otherwise.
2. Run cells top to bottom. Cell 1 mounts Drive at `/content/drive/MyDrive`, loads
   `My Drive/Colab Notebooks/Mistraton/.env` (never commit it), sets
   `SENTINEL_DATA_DIR`/`SENTINEL_ARTIFACTS_DIR` to Drive-persistent paths, and adds
   `…/Mistraton/backend` to `sys.path` — **no repo copy** (the Drive FS is slow per
   file). Only the frontend skeleton (~36 files: `package.json`, `src/`, `public/`,
   configs) is copied to `/content/frontend` because `npm install/build` needs local disk.
3. Cell 2 pip-installs from absolute Drive paths; cell 3 builds the frontend
   (`npm run build` → `/content/frontend/dist`).
4. Cell 4 starts uvicorn on `0.0.0.0:8000` in a daemon thread.
5. Cell 5 prints the public `https://….trycloudflare.com` URL — open it.

## Website flow

**Upload** an incident bundle (files → `data/raw/<incident_id>/` → `records.jsonl`)
→ **Pipeline** buttons run each CLI stage as a background job with live logs →
**Watch** posts metric streams to `/sentinel/watch` (shadow mode supported) and
review alerts → **Timestral** chats with the QLoRA checkpoint saved by
`finetune-timestral` to `data/artifacts/timestral-hf/`.

## Gotchas

- Free Colab: ~90 min idle disconnect, 12 h hard limit; the tunnel URL changes every
  session → re-run cell 5. Keep the tab open.
- `SENTINEL_API_KEY` (optional, in Drive `.env`): when set, mutating endpoints
  (`upload`, `pipeline`, `timestral`, `feedback`) require an `X-API-Key` header; the
  key is injected into the served `index.html` as `window.__SENTINEL_KEY__`.
- `finetune-timestral` needs the GPU deps from `backend/requirements-colab.txt`
  (installed in cell 2) and a built dataset (`build-dataset`). Base model is
  configurable via `--base-model` / `TIMESTRAL_BASE_MODEL` (default SmolLM2-135M).
- Only the train split is used for fine-tuning; the held-out incident test set is
  never touched (same rule as `tuning/train.py`).

## Local equivalent

```sh
cd backend
pip install -r requirements.txt
SENTINEL_DATA_DIR=./data SENTINEL_DIST_DIR=../frontend/dist \
  python colab_app.py --host 0.0.0.0 --port 8000
```

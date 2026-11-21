# DEV INSTRUCTIONS — Le Chonk in London submission (21 Nov 2026)

Branch `vibe/submission-ready` is based on `main @ 8ad1f3b` + README submission header + this file.
**Safe to merge to main later: it is 0 commits behind, only ahead.** Verify: `git rev-list --left-right --count origin/main...HEAD` → `0 <n>`.

## 1. Push & PR (needs an account with write access to mistraton/Mistraton — dimknaf does NOT have it)
```bash
git push -u origin vibe/submission-ready   # from a machine with teammate credentials / gh auth
# then open PR: vibe/submission-ready -> main
```

## 2. Voice (Special_03, +£250) — optional but prepped
- Voice code lives on `origin/vibe/voice-cribella-7ecf71` (`backend/voice.py`, `frontend/src/voice.js`, `.env.example` entries). If not merged into this branch yet: `git merge origin/vibe/voice-cribella-7ecf71` (resolve conflicts in `backend/main.py`, `.env.example`, `README.md` in favour of keeping BOTH the sentinel router and the voice router mounts).
- Activate on the VM only, never in git:
  - redeem credits: `LECHONK-LONDON-NOV-26-CRED` at admin.mistral.ai/subscription/upgrade/redeem
  - create API key: console.mistral.ai/api-keys
  - add to VM `.env`: `MISTRAL_API_KEY=sk-...`, `VOICE_MODEL=voxtral-mini-tts-3-0`, `VOICE_DEFAULT=Bea Cribella`
  - `docker compose restart api` (or `up -d`)
  - verify: `GET https://safestral.dpdns.org/api/voice/status` → `voice.enabled: true`; `POST /api/voice/say {"text":"..."}` → mp3
- **Claim Special_03 only if `/api/voice/status` shows enabled:true at submit time.**

## 3. Demo access for judges
- Live: https://safestral.dpdns.org/ — HTTP Basic auth, username `safestral`, password = `SENTINEL_WEB_PASSWORD` from the VM `.env`. **Put these in the submission notes.**
- Dashboard panels: Watch (live monitor + presets Relaxed/Balanced/Strict), Pipeline, Timestral (forecast bands), Upload. Alert = incident card in "Alerts and review" with Detected-by / Reviewed-by / evidence IDs / timestamps.

## 4. Submission (HARD deadline 18:00 GMT; submit by ~17:40)
- Portal link: **not in the guide/Luma — get it from Mistral Discord (#announcements) or an organizer on site.**
- Fields: name SAFETRAL · tagline · description · repo https://github.com/mistraton/Mistraton · live URL · **demo video URL (unlisted YouTube/Loom — the video is the real Round-1 gate)** · Basic-auth credentials note · teammates (teams 3–5, add all)
- Tracks: Track_01 always; Special_03 only if voice verified; Bonus_02 (Agents/Workflows, +£500) **only if genuinely used — say where**.
- Video: record logged-in dashboard → start sentinel run → alert card → voice playback if enabled. ≤3–4 min.

## 5. Hygiene (must stay true)
```bash
git grep -nE "(sk-[A-Za-z0-9]{20,}|[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{20,})" -- .   # empty
git ls-files | grep -E "^\.env$|auth\.json|credentials"                              # empty
```
CI (Actions) is red because deploy needs VM secrets — README already says so; app is live.

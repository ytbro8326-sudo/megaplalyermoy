# MegaPlayer

HLS · MP4 · IFrame player with a Python proxy backend.  
Supports Referer/Origin spoofing, per-host saved rules, and foxy-doxy / custom proxy modes.

---

## Run locally

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:6789
```

---

## Deploy to Render (web service)

### Step 1 — Create the service

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Render auto-reads `render.yaml` — no extra config needed
4. Click **Create Web Service**
5. Wait ~2 min for first build. Your URL will be:  
   `https://megaplayer.onrender.com` (or whatever Render assigns)

### Step 2 — Get the Deploy Hook (for GitHub Actions CI)

1. Render Dashboard → your service → **Settings** → scroll to **Deploy Hook**
2. Copy the hook URL (looks like `https://api.render.com/deploy/srv-xxx?key=yyy`)

### Step 3 — Add secret to GitHub

1. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**
2. Name: `RENDER_DEPLOY_HOOK_URL`
3. Value: paste the Render deploy hook URL

Now every push to `main`:
- GitHub Actions runs the smoke test
- If it passes, it calls the Render hook → triggers a fresh production deploy

---

## File structure

```
megaplayer/
├── app.py                        ← Flask server (proxy + API + static serving)
├── megaplayer.html               ← Player UI (served at /)
├── profile.json                  ← Saved per-host rules (seeded, persists on disk)
├── requirements.txt
├── Procfile                      ← gunicorn start command
├── render.yaml                   ← Render deploy config
├── .gitignore
└── .github/
    └── workflows/
        └── deploy.yml            ← CI smoke-test + Render deploy trigger
```

---

## API reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serve player HTML |
| `/proxy?url=...&ref=...&origin=...` | GET | Proxy HLS manifest or segment with spoofed headers |
| `/api/profiles` | GET | List all saved host rules |
| `/api/profiles` | POST | Save/update a host rule (body: `{host, ref, origin, proxyMode, proxyFmt, customUrl}`) |
| `/api/profiles/<host>` | DELETE | Delete a host rule |
| `/results` | GET | Serve `results.json` if present |
| `/health` | GET | Health check (returns `{"status":"ok"}`) |

---

## Notes

- **profile.json is committed** — your saved CDN rules are seeded on fresh deploys
- **Render free plan**: the filesystem resets on each redeploy. Rules saved in-browser via the UI will persist in `localStorage` as fallback automatically. To persist server-side rules across deploys, either commit `profile.json` changes or upgrade to a paid Render plan and uncomment the `disk:` section in `render.yaml`
- The `/proxy` endpoint rewrites all m3u8 internal URLs (segments, sub-manifests, `#EXT-X-KEY URI=`) so they also route through the proxy — full HLS chain works end to end

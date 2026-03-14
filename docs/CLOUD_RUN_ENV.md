# Cloud Run: Redis (Redis Cloud) and Postgres (Render) env vars

Use your existing **Redis Cloud** and **Render Postgres** with the four Cloud Run services. If you see latency issues later, you can switch to GCP (Memorystore + Cloud SQL).

**Persisting env vars:** The Cloud Build config does **not** set env vars on deploy (so rebuilds only update the image). Set `REDIS_*`, `DB_URI`, and any API keys once in the Cloud Run console (or via `gcloud run services update`); they will **persist across all future rebuilds**.

---

## LLM / STT / TTS / SuiteCRM / Redis / Postgres (where to put them)

API keys and secrets are **not** in the repo or in the build. Set them as **environment variables** on each Cloud Run service. Because the build no longer overwrites env vars, whatever you set in the console (or via `gcloud run services update`) **persists across rebuilds**.

**Reference:** See `.env.cloudrun.example` in the repo for the full list of variable **names** (no real values). Copy it to `.env.cloudrun`, fill in locally, then set in Cloud Run per service below. **Never commit `.env.cloudrun`** or paste real keys into the repo.

**Important:** `REDIS_DB` must be a **number** (0–15). For Redis Cloud single DB use `REDIS_DB=0`. A value like `database-MH3YNEOB` is a Render DB name, not a Redis DB index.

### By service

| Service | Env vars to set |
|--------|------------------|
| **voice-gateway-service** | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB=0`, `DB_URI`; `AGENT_LLM_URL` (agent-llm URL); Twilio: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `TWILIO_TRANSFER_CALLER_ID`, `FREEPBX_DOMAIN`. Optional STT/TTS if wired: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`, etc. |
| **agent-llm-service** | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB=0`, `DB_URI`; LLM: `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_MODEL`; optional for provider checks: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`, `CARTESIA_*`. |
| **call-center-service** | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB=0`, `DB_URI` (if you add DB/Redis). Optional: `CONVONET_API_BASE`, `VOICE_ASSISTANT_URL`, `MORTGAGE_DASHBOARD_URL` for links (see below). |
| **crm-integration-service** | `REDIS_*` if used; SuiteCRM: `SUITECRM_BASE_URL`, `SUITECRM_CLIENT_ID`, `SUITECRM_CLIENT_SECRET`, `SUITECRM_USERNAME`, `SUITECRM_PASSWORD`. Optional: `DATABASE_URL` (MySQL for SuiteCRM). |

**CONVONET_API_BASE, VOICE_ASSISTANT_URL, MORTGAGE_DASHBOARD_URL (single domain v2.convonetai.com):**  
When all traffic is on one domain with path-based routing, **do not set** these three on call-center-service (leave them unset). The app then uses relative paths: API base becomes same-origin (empty prefix), voice assistant link `/voice_assistant`, mortgage link `/mortgage_dashboard`. Only set them if the frontend is served from a different origin than the API or you use different hostnames for those pages.

### How to set (one-time per service)

- **Console:** Cloud Run → select service → **Edit & deploy new revision** → **Variables & secrets** → add each variable (paste value from your local `.env.cloudrun` or secret store).
- **CLI:** Use `--update-env-vars="KEY1=value1,KEY2=value2"`. If a value contains commas, use the `^@^` delimiter:  
  `--update-env-vars='^@^REDIS_HOST=host@REDIS_PORT=17434@REDIS_PASSWORD=secret@REDIS_DB=0@DB_URI=postgresql://...'`

For production, use **Secret Manager** and `--set-secrets` so secrets don’t appear in the revision config.

---

## Env vars the app expects

| Variable        | Used by              | Example (yours) |
|----------------|----------------------|------------------|
| `REDIS_HOST`   | All services         | `redis-17434.c124.us-central1-1.gce.redns.redis-cloud.com` |
| `REDIS_PORT`   | All services         | `17434` |
| `REDIS_PASSWORD` | All services       | *(secret)* |
| `REDIS_DB`     | All services         | Numeric Redis DB index `0`–`15`. If your Redis Cloud has one logical DB, use `0`. |
| `DB_URI`       | Agent-LLM, Call Center, etc. | `postgresql://user:pass@host/dbname` (Render connection string) |

**Note:** In code, `REDIS_DB` is read as a number (default `0`). Redis Cloud usually exposes a single DB as index `0`. If you have a logical name like `database-MH3YNEOB`, that’s not a Redis DB index—use `0` for `REDIS_DB` in Cloud Run.

---

## Set env vars once (they persist across rebuilds)

The build only updates the container image; it does **not** set or overwrite env vars. Set them once and they stay.

1. Create a local file that won’t be committed, e.g. `./.env.cloudrun`:

   ```bash
   # .env.cloudrun (DO NOT COMMIT – add to .gitignore if you want)
   REDIS_HOST=redis-17434.c124.us-central1-1.gce.redns.redis-cloud.com
   REDIS_PORT=17434
   REDIS_PASSWORD=your_redis_password
   REDIS_DB=0
   DB_URI=postgresql://user:pass@host/dbname
   ```

2. Source it and run the build (one line):

   ```bash
   set -a && source ./.env.cloudrun && set +a && \
   gcloud builds submit --config cloudbuild.yaml . \
     --substitutions=COMMIT_SHA=latest,_REDIS_HOST="$REDIS_HOST",_REDIS_PORT="$REDIS_PORT",_REDIS_PASSWORD="$REDIS_PASSWORD",_REDIS_DB="$REDIS_DB",_DB_URI="$DB_URI"
   ```

   On macOS/zsh you can use:

   ```bash
   set -a; source ./.env.cloudrun; set +a
   gcloud builds submit --config cloudbuild.yaml . \
     --substitutions=COMMIT_SHA=latest,_REDIS_HOST="$REDIS_HOST",_REDIS_PORT="$REDIS_PORT",_REDIS_PASSWORD="$REDIS_PASSWORD",_REDIS_DB="$REDIS_DB",_DB_URI="$DB_URI"
   ```

3. Optional: add `.env.cloudrun` to `.gitignore` so it’s never committed:

   ```
   .env.cloudrun
   ```

After setting vars once (console or `gcloud run services update` above), run `gcloud builds submit --config cloudbuild.yaml .` whenever you rebuild; env vars will not be reset. For production, use Secret Manager below.

---

## Production: use Secret Manager (recommended)

So that secrets don’t appear in build logs or substitution history:

1. Create secrets in Secret Manager (e.g. in Google Cloud Console or gcloud):

   ```bash
   echo -n "your_redis_password" | gcloud secrets create redis-password --data-file=-
   echo -n "postgresql://user:pass@host/dbname" | gcloud secrets create db-uri --data-file=-
   ```

2. Grant the Cloud Run runtime service account access to the secrets (e.g. `roles/secretmanager.secretAccessor`).

3. Deploy **without** passing `_REDIS_PASSWORD` or `_DB_URI` in substitutions. After the first deploy (or in a separate step), update the service to use secrets:

   ```bash
   gcloud run services update agent-llm-service --region=us-central1 \
     --set-secrets=REDIS_PASSWORD=redis-password:latest,DB_URI=db-uri:latest
   ```

   Repeat for `voice-gateway-service`, `call-center-service`, and `crm-integration-service` if they need DB/Redis. Set non-secret vars with `--update-env-vars`:

   ```bash
   gcloud run services update agent-llm-service --region=us-central1 \
     --update-env-vars=REDIS_HOST=redis-17434.c124.us-central1-1.gce.redns.redis-cloud.com,REDIS_PORT=17434,REDIS_DB=0
   ```

---

## Voice gateway → Agent LLM URL

For the Twilio/WebSocket flow, voice-gateway calls the agent-llm service. If they’re in the same project/region, set `AGENT_LLM_URL` on the voice-gateway service to the agent-llm Cloud Run URL (or the internal URL if using VPC). You can add it via substitutions later or in the console:

- `AGENT_LLM_URL=https://agent-llm-service-XXXXX-uc.a.run.app`  
  (use the URL from the Cloud Run console for `agent-llm-service`)

---

## Switching to GCP later (Memorystore + Cloud SQL)

When you’re ready to reduce latency:

1. Create a Memorystore (Redis) instance and a Cloud SQL (Postgres) instance in the same region as Cloud Run (e.g. `us-central1`).
2. Put the new connection details in Secret Manager (or env vars) and redeploy/update the four services with the new `REDIS_*` and `DB_URI` values.
3. Optionally use a VPC connector so Cloud Run can reach Memorystore/Cloud SQL private IPs.

No code changes are required; the app already reads these from the environment.

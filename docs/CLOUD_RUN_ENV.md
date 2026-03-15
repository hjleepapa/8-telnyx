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
| **call-center-service** | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB=0` (required for **Agent Monitor** to show interactions—agent-llm writes to Redis, call-center reads). `DB_URI` if you add DB. Optional: `CONVONET_API_BASE`, `VOICE_ASSISTANT_URL`, `MORTGAGE_DASHBOARD_URL` for links; `SIP_DOMAIN`, `SIP_WSS_PORT` for Call Center UI (SIP server for agents). |
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
| `DB_URI`       | Agent-LLM, Call Center, Voice Gateway, etc. | `postgresql://user:pass@host/dbname` (Render connection string) |

**Render PostgreSQL – use full hostname:** If you use Render.com Postgres, `DB_URI` must use the **full external hostname**, not the short internal one. In the Render dashboard, use the **External** connection string. The host must look like `dpg-xxxx-a.oregon-postgres.render.com` (or your region). If you use only `dpg-d0nkb5jipnbc7393afi0-a` (no domain), you will get **"could not translate host name ... to address: Temporary failure in name resolution"**. **Fix (either):** (1) Set `DB_URI` to the full URL, e.g. `postgresql://user:pass@dpg-d0nkb5jipnbc7393afi0-a.oregon-postgres.render.com/posts_8uci`; or (2) keep the short host in `DB_URI` and set **`RENDER_POSTGRES_HOST_SUFFIX=.oregon-postgres.render.com`** (or your region) on the service so the app appends it for DNS (used by agent-llm-service / mortgage tools).

**Voice PIN (voice-gateway-service):** When `ENABLE_VOICE_PIN=true`, the WebSocket voice assistant requires a PIN before Start. If `DB_URI` is set, the PIN is validated against the **`users_anthropic`** table (column `voice_pin`, active users only). The authenticated user’s `id` and name are then used for the agent and mortgage tools. If `DB_URI` is not set, the env var `VOICE_PIN` is used as the only valid PIN. Set `DB_URI` on voice-gateway to use your Postgres (e.g. `jdbc:postgresql://...` → use the same URL as `postgresql://...` for SQLAlchemy).

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

## Twilio transfer to FusionPBX (voice-gateway-service)

When a user on a **Twilio voice call** says “transfer me to an agent,” the agent returns a transfer marker and the voice-gateway returns TwiML that Dials the SIP endpoint (e.g. `2001@FusionPBX`). Set these on **voice-gateway-service**:

| Variable | Purpose |
|----------|---------|
| `VOICE_GATEWAY_PUBLIC_URL` or `WEBHOOK_BASE_URL` | Base URL Twilio uses for webhooks (e.g. `https://voice-gateway-service-xxx.run.app` or `https://v2.convonetai.com`). Used for the transfer callback URL. |
| `FREEPBX_DOMAIN` or `FUSIONPBX_SIP_DOMAIN` | FusionPBX host (IP or FQDN) for SIP, e.g. `pbx.example.com` or `136.115.41.45`. |
| `FUSIONPBX_SIP_TRANSPORT` | Optional; `udp` (default) or `tcp`. |
| `TRANSFER_TIMEOUT` | Optional; seconds to wait for the agent to answer (default `30`). |
| `TWILIO_TRANSFER_CALLER_ID` or `TWILIO_PHONE_NUMBER` | Caller ID presented to FusionPBX when dialing the extension. |
| `FREEPBX_SIP_USERNAME` / `FREEPBX_SIP_PASSWORD` | Optional; SIP auth if FusionPBX requires it (otherwise whitelist Twilio IPs). |

Flow: `/twilio/process_audio` receives the agent response with `transfer_marker` → parses `TRANSFER_INITIATED:extension|department|reason` → returns TwiML with `<Dial><Sip>sip:extension@domain</Sip></Dial>`. When the Dial ends, Twilio POSTs to `{VOICE_GATEWAY_PUBLIC_URL}/twilio/transfer_callback?extension=...` (if base URL is set).

---

## Switching to GCP later (Memorystore + Cloud SQL)

When you’re ready to reduce latency:

1. Create a Memorystore (Redis) instance and a Cloud SQL (Postgres) instance in the same region as Cloud Run (e.g. `us-central1`).
2. Put the new connection details in Secret Manager (or env vars) and redeploy/update the four services with the new `REDIS_*` and `DB_URI` values.
3. Optionally use a VPC connector so Cloud Run can reach Memorystore/Cloud SQL private IPs.

No code changes are required; the app already reads these from the environment.

---

## Artifact Registry: automatic removal of old container images

After each full deploy (`gcloud builds submit --config cloudbuild.yaml .`), **Cloud Build runs a cleanup step** that removes old container image tags for all four services. For each package (`voice-gateway-service`, `agent-llm-service`, `call-center-service`, `crm-integration-service`), it keeps only the tag you just deployed (e.g. `latest` or `$_COMMIT_SHA`) and deletes any other tags. That prevents old tags from accumulating when you use unique tags per build.

**Optional: remove untagged images**  
When you always push the same tag (e.g. `latest`), the previous digest becomes untagged. To have GCP automatically delete those, set a **cleanup policy** on the repository once: **Console** → Artifact Registry → **convonet-repo** → **Cleanup policies** → Add policy → “Keep most recent versions” → set **1** → Save. You can also use `gcloud artifacts repositories set-cleanup-policies` with a JSON policy file (see [Cleanup policy overview](https://cloud.google.com/artifact-registry/docs/repositories/cleanup-policy-overview)).

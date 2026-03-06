# SuiteCRM Integration Guide

Convonet integrates with [SuiteCRM](http://34.9.14.57) for doctor appointments, patient onboarding, and clinical notes. This guide covers OAuth2 setup and troubleshooting.

## Environment Variables

Set these in `.env` (local) and **Render Dashboard > Your Service > Environment** (production):

```bash
SUITECRM_BASE_URL=http://34.9.14.57
SUITECRM_CLIENT_ID=95172a83-7397-418d-bd9b-99c5e8051050
SUITECRM_CLIENT_SECRET=convonetai#1234
SUITECRM_USERNAME=admin          # SuiteCRM admin username
SUITECRM_PASSWORD=your_password   # SuiteCRM admin password
```

**Render:** `render.yaml` lists these with `sync: false` – you must add the values manually in the Render dashboard. If missing, `check_patient_exists` and other SuiteCRM tools will return "Authentication failed". Check Render logs for `⚠️ MCP config: SUITECRM_* NOT SET` at startup.

## OAuth2 RSA Keys (Required)

SuiteCRM uses RSA keys to sign JWT tokens. **If keys are missing or invalid, authentication will fail** with 500 errors or "key path does not exist."

### 1. Locate the OAuth2 Directory

On your SuiteCRM server, the keys go in one of these locations (varies by version):

- `lib/API/OAuth2/` (common)
- `Api/V8/OAuth2/`
- `{{suitecrm.root}}/Api/V8/OAuth2/`

### 2. Generate Keys

SuiteCRM docs recommend 1024-bit keys; 2048-bit also works on newer versions:

```bash
cd /path/to/suitecrm/lib/API/OAuth2   # or your OAuth2 dir

# Option A: 1024-bit (per SuiteCRM docs, best compatibility)
openssl genrsa -out private.key 1024
openssl rsa -in private.key -pubout -out public.key

# Option B: 2048-bit (more secure, if 1024 fails try this)
# openssl genrsa -out private.key 2048
# openssl rsa -in private.key -pubout -out public.key

chmod 600 private.key public.key
chown www-data:www-data private.key public.key   # or apache:apache
```

### 3. Verify Permissions

- `private.key` and `public.key` must be readable by the web server (e.g. `www-data` or `apache`)
- Typical permissions: `600` or `660`
- The directory must not be web-accessible (no direct HTTP access)

### 4. Check Keys Exist

```bash
ls -la /path/to/suitecrm/lib/API/OAuth2/
# Should show: private.key  public.key
```

## OAuth2 Client Setup (SuiteCRM Admin)

1. Go to **Administration > OAuth2 Clients and Tokens**
2. Create **New Password Client**

| Field | Value |
|-------|-------|
| Name | convonetai |
| Secret | convonetai#1234 |
| Is Confidential | Yes |
| Allowed Grant Type | Password Grant |

3. Save. The **Client ID** (GUID) is shown after save. Use it as `SUITECRM_CLIENT_ID`.

**Note:** The secret is hashed when saved. Use the exact value you entered when creating the client.

## Token URL Paths

SuiteCRM versions use different token endpoints. The client tries:

1. `{base_url}/Api/access_token` (7.10)
2. `{base_url}/api/oauth/access_token` (V8)
3. `{base_url}/legacy/Api/access_token` (fallback)

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| 500 Internal Server Error | Missing/invalid RSA keys | Generate keys in `lib/API/OAuth2/` |
| "Key path does not exist" | Wrong path or permissions | Check path, verify `chmod 600` |
| "The password is invalid" | Wrong username/password | Use valid SuiteCRM user credentials |
| 401 Unauthorized | Invalid client_id/secret | Recreate OAuth2 client, ensure secret matches |
| 404 Not Found | Wrong token URL | Check SuiteCRM version path |

## Test Authentication

```bash
# From project root with .env configured
python scripts/test_suitecrm.py
```

Or inline:
```bash
python -c "
from convonet.services.suitecrm_client import SuiteCRMClient
c = SuiteCRMClient()
if c.authenticate():
    print('✅ SuiteCRM auth OK')
else:
    print('❌ Auth failed - check logs for RSA key or credential errors')
"
```

### Raw token request (diagnostics)

If auth fails, run this to see the exact error from SuiteCRM:

```bash
curl -X POST "http://34.9.14.57/Api/access_token" \
  -H "Content-Type: application/vnd.api+json" \
  -H "Accept: application/json" \
  -d '{"grant_type":"password","client_id":"YOUR_CLIENT_ID","client_secret":"YOUR_SECRET","username":"admin","password":"YOUR_PASS"}'
```

- **400 "invalid_scope"** → Omit scope (SuiteCRM scopes not implemented yet)
- **500 + "key" or "path"** → RSA keys missing in `lib/API/OAuth2/`
- **"Authentication failed" on Render** → 1) Check Render Dashboard has all SUITECRM_* vars. 2) Ensure SuiteCRM at 34.9.14.57 is reachable from the internet (Render runs in cloud; private IPs won't work).
- **401 "password invalid"** → Wrong username/password
- **401 "invalid client"** → Wrong client_id or client_secret

## MCP Tools (SuiteCRM)

- `book_appointment` – Schedule a Meeting for a Contact
- `onboard_patient` – Create a new Contact
- `check_patient_exists` – Search Contact by phone
- `log_clinical_intake` – Create a Case
- `save_call_summary` – Create a Note

These are available to the Healthcare agent when SuiteCRM is configured.

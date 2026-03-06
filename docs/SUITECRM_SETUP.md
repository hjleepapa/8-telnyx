# SuiteCRM Integration Setup

Convonet integrates with [SuiteCRM](http://34.9.14.57) to create doctor appointments when users book via the Healthcare voice agent.

## OAuth2 Configuration (SuiteCRM Admin)

1. Go to **Administration → OAuth2 Clients and Tokens**
2. Create a **Password Grant** client:
   - **Name**: convonetai (or any name)
   - **Secret**: convonetai#1234 (or your chosen secret)
   - **Allowed Grant Type**: Password Grant
   - **Is Confidential**: Yes
3. Save and note the **Client ID** (GUID, e.g. `95172a83-7397-418d-bd9b-99c5e8051050`)

## Environment Variables

Set these in your `.env` (local) or Render Environment:

| Variable | Value | Description |
|----------|-------|-------------|
| `SUITECRM_BASE_URL` | `http://34.9.14.57` | SuiteCRM instance URL |
| `SUITECRM_CLIENT_ID` | `95172a83-7397-418d-bd9b-99c5e8051050` | OAuth2 client ID from admin |
| `SUITECRM_CLIENT_SECRET` | `convonetai#1234` | The secret you set when creating the client |
| `SUITECRM_USERNAME` | `admin` | SuiteCRM user (must have API access) |
| `SUITECRM_PASSWORD` | `your_password` | That user's password |

## Example .env

```bash
SUITECRM_BASE_URL=http://34.9.14.57
SUITECRM_CLIENT_ID=95172a83-7397-418d-bd9b-99c5e8051050
SUITECRM_CLIENT_SECRET=convonetai#1234
SUITECRM_USERNAME=admin
SUITECRM_PASSWORD=your_suitecrm_user_password
```

## How It Works

- **Healthcare Agent** uses `book_appointment` when the user asks to schedule a doctor visit
- `book_appointment(patient_id, appointment_type, date_start, duration)` creates a Meeting in SuiteCRM linked to the Contact
- Patient must exist in SuiteCRM (Contacts) first; use `check_patient_exists(phone)` or `onboard_patient(...)` if needed

## Tools Available

| Tool | Description |
|------|-------------|
| `check_patient_exists` | Find patient by phone number |
| `onboard_patient` | Create new patient (Contact) |
| `book_appointment` | Schedule Meeting for a Contact |
| `log_clinical_intake` | Create Case for triage/symptoms |
| `save_call_summary` | Save Note with call summary |

## Troubleshooting

- **401 Unauthorized**: Check client_id, client_secret, username, password
- **404 on /Api/access_token**: Try legacy path; ensure mod_rewrite and API are enabled
- **Patient not found**: Use `onboard_patient` first, or ensure the Contact exists in SuiteCRM

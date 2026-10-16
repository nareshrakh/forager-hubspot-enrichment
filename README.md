# Forager × HubSpot Enrichment (Python / FastAPI)

Custom-coded automation (no n8n). Covers all 5 tasks:

| # | Task | Where |
|---|------|-------|
| 1 | Enrich company | `EnrichmentService.enrich_company` · `POST /enrich/company` |
| 2 | Enrich contact | `enrich_contact` · `POST /enrich/contact` |
| 3 | Find contacts (title filter) | `find_contacts` · `POST /find-contacts` |
| 4 | Company webhook | `POST /webhooks/hubspot` (`company.creation`) |
| 5 | Contact webhook | `POST /webhooks/hubspot` (`contact.creation`) |

## Setup

**1. Forager** – sign up (150 free credits), create an API key, note your numeric `account_id`
(`GET https://api-v2.forager.ai/api/users/current/` with header `X-API-KEY` shows it).

**2. HubSpot** – use a free developer test account.
Settings → Integrations → Private Apps → Create. Scopes:
`crm.objects.companies.read/write`, `crm.objects.contacts.read/write`, `crm.schemas.companies.write`, `crm.schemas.contacts.write`.
Copy the access token and the app's client secret.

**3. Run locally**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
uvicorn app.main:app --reload
```
Custom properties (`forager_*`, `person_linkedin_url`, `employer_domain`, `employer_linkedin_url`) are created automatically on startup.

**4. Primary deliverable (OpenAI + 5 people)**
```bash
python -m scripts.demo_openai                       # default title filter
python -m scripts.demo_openai --titles "CEO,CTO,VP" # custom title filter
```

**5. Deploy on Railway**
Push to GitHub → Railway → New Project → Deploy from repo → add the env vars from `.env.example` →
Settings → Networking → Generate Domain. Set `PUBLIC_BASE_URL` to that domain.

**6. Webhooks** – In the HubSpot Private App → Webhooks tab: target URL
`https://<railway-domain>/webhooks/hubspot`, subscribe to **Company created** and **Contact created**.

## How it behaves
- **Company created** → enrich company → if contacts already associated, enrich them; otherwise find contacts (title-filtered, max `MAX_CONTACTS`) and enrich.
- **Contact created** → enrich that contact (resolved via LinkedIn URL, else name + company domain).
- **Loop/double-charge guard**: records get `forager_status`; contacts we create are marked `pending`, so their creation webhook is ignored.
- **Job-title filtering**: Forager boolean `role_title` query + strict client-side include/exclude check. Override per call: `{"include_titles": ["CTO"], "exclude_titles": ["Intern"]}`.
- Webhooks return 200 immediately; work runs in a background task. Signature verified (HubSpot v3 HMAC), events de-duplicated.

## Demo video script (3–5 min)
1. Show repo + Railway deploy + env vars (blur values).
2. Run `scripts.demo_openai`; show OpenAI company with all fields in HubSpot.
3. Show 5 contacts: name, LinkedIn, title, company/domain/company LinkedIn, email, phone, city/state/country.
4. Title filter: `curl -X POST .../find-contacts -H "X-API-Secret: …" -d '{"company_id":"…","include_titles":["CTO"],"limit":2}'`.
5. Webhooks: create a new test company in HubSpot UI → Railway logs show webhook → company enriched → contacts found. Then create a new contact → enriched.

## Extras to flag for discussion (stored in `forager_*` custom properties, toggle with `ENABLE_EXTRAS`)
- Person: headline, **About/description**, skills, education, plus the full role history, certifications, languages, honors, publications, patents, projects, volunteering (available in `person_detail_lookup`, not stored yet).
- Company: LinkedIn industry, keywords, operating status, founded date, domain rank/traffic, logo; funding events, job posts and web technologies are available through extra search filters / `website_detail_lookup`.
- Forager's work-email lookup is async and only charges when an address is found; verify credit costs per endpoint in the dashboard before large runs.

## Assumptions to verify on first run
Forager response shapes for `emails` and `location.osm_locations` were taken from the OpenAPI docs; if a field comes back differently, adjust `app/mapping.py` / `app/forager.py` (small, isolated spots).

"""Primary deliverable: enrich OpenAI + 5 people under it.

Usage:  python -m scripts.demo_openai [--titles "CEO,CTO,VP"]
Creates the OpenAI company in HubSpot (if missing), enriches it, then finds and
enriches 5 people (title-filtered).
"""
import argparse
import json

from app.config import get_settings
from app.forager import ForagerClient
from app.hubspot import HubSpotClient
from app.services import EnrichmentService


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="openai.com")
    ap.add_argument("--name", default="OpenAI")
    ap.add_argument("--titles", default=None, help="comma-separated include titles")
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args()

    s = get_settings()
    hs = HubSpotClient(s.hubspot_token)
    hs.ensure_properties()
    svc = EnrichmentService(s, ForagerClient(s.forager_api_key, s.forager_account_id, s.forager_base_url), hs)

    company = hs.find_one("companies", "domain", a.domain, ["name"])
    if not company:  # status guard stops the webhook from double-spending credits
        company = hs.create("companies", {"name": a.name, "domain": a.domain, "forager_status": "pending"})
    cid = company["id"]

    print("Company:", json.dumps(svc.enrich_company(cid), indent=2))
    include = s.split(a.titles) if a.titles else None
    people = svc.find_contacts(cid, include=include, limit=a.limit)
    print("Contacts:", json.dumps(people, indent=2))


if __name__ == "__main__":
    main()

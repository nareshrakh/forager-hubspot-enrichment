"""Thin client for the HubSpot CRM v3/v4 APIs (Private App token)."""
import logging
import time

import httpx

log = logging.getLogger(__name__)

# Custom properties the automation writes. Created on startup if missing.
CUSTOM_PROPERTIES: dict[str, list[tuple]] = {
    # (name, label, type, fieldType)
    "contacts": [
        ("forager_status", "Forager Status", "string", "text"),
        ("forager_person_id", "Forager Person ID", "string", "text"),
        ("person_linkedin_url", "Person LinkedIn URL", "string", "text"),
        ("employer_domain", "Employer Domain", "string", "text"),
        ("employer_linkedin_url", "Employer LinkedIn URL", "string", "text"),
        # nice-to-have extras (flagged for discussion)
        ("forager_headline", "Forager Headline", "string", "text"),
        ("forager_about", "Forager About", "string", "textarea"),
        ("forager_skills", "Forager Skills", "string", "textarea"),
        ("forager_education", "Forager Education", "string", "textarea"),
    ],
    "companies": [
        ("forager_status", "Forager Status", "string", "text"),
        ("forager_org_id", "Forager Org ID", "string", "text"),
        # nice-to-have extras (flagged for discussion)
        ("forager_industry", "Forager Industry", "string", "text"),
        ("forager_keywords", "Forager Keywords", "string", "textarea"),
        ("forager_operating_status", "Forager Operating Status", "string", "text"),
        ("forager_founded_date", "Forager Founded Date", "string", "text"),
        ("forager_domain_rank", "Forager Domain Rank", "number", "number"),
    ],
}
GROUPS = {"contacts": "contactinformation", "companies": "companyinformation"}


class HubSpotClient:
    def __init__(self, token: str):
        self.http = httpx.Client(
            base_url="https://api.hubapi.com",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(4):
            r = self.http.request(method, path, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            return r
        return r

    # ---- CRUD ------------------------------------------------------------
    def get(self, obj: str, oid: str, props: list[str]) -> dict:
        r = self._req("GET", f"/crm/v3/objects/{obj}/{oid}", params={"properties": ",".join(props)})
        r.raise_for_status()
        return r.json()

    def create(self, obj: str, props: dict) -> dict:
        r = self._req("POST", f"/crm/v3/objects/{obj}", json={"properties": _clean(props)})
        r.raise_for_status()
        return r.json()

    def update(self, obj: str, oid: str, props: dict) -> dict:
        r = self._req("PATCH", f"/crm/v3/objects/{obj}/{oid}", json={"properties": _clean(props)})
        r.raise_for_status()
        return r.json()

    def find_one(self, obj: str, prop: str, value: str, props: list[str]) -> dict | None:
        body = {
            "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}],
            "properties": props,
            "limit": 1,
        }
        r = self._req("POST", f"/crm/v3/objects/{obj}/search", json=body)
        r.raise_for_status()
        results = r.json().get("results") or []
        return results[0] if results else None

    # ---- associations ----------------------------------------------------
    def associated_ids(self, from_obj: str, oid: str, to_obj: str) -> list[str]:
        r = self._req("GET", f"/crm/v4/objects/{from_obj}/{oid}/associations/{to_obj}")
        r.raise_for_status()
        return [str(x["toObjectId"]) for x in r.json().get("results", [])]

    def associate(self, from_obj: str, from_id: str, to_obj: str, to_id: str) -> None:
        r = self._req(
            "PUT", f"/crm/v4/objects/{from_obj}/{from_id}/associations/default/{to_obj}/{to_id}"
        )
        r.raise_for_status()

    # ---- schema ----------------------------------------------------------
    def ensure_properties(self) -> None:
        for obj, defs in CUSTOM_PROPERTIES.items():
            for name, label, ptype, field in defs:
                r = self._req(
                    "POST",
                    f"/crm/v3/properties/{obj}",
                    json={
                        "name": name,
                        "label": label,
                        "type": ptype,
                        "fieldType": field,
                        "groupName": GROUPS[obj],
                    },
                )
                if r.status_code not in (200, 201, 409):
                    log.warning("could not create %s.%s: %s", obj, name, r.text[:200])


def _clean(props: dict) -> dict:
    return {k: v for k, v in props.items() if v not in (None, "")}

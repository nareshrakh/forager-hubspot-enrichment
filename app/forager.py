"""Thin client for the Forager API (https://docs.forager.ai)."""
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ForagerError(Exception):
    pass


def title_matches(title: str | None, include: list[str], exclude: list[str]) -> bool:
    """Client-side guarantee for job-title filtering (server query is best-effort)."""
    t = (title or "").lower()
    if any(x.lower() in t for x in exclude):
        return False
    return not include or any(i.lower() in t for i in include)


class ForagerClient:
    def __init__(self, api_key: str, account_id: int, base_url: str):
        self.acct = account_id
        self.http = httpx.Client(
            base_url=base_url, headers={"X-API-KEY": api_key}, timeout=60
        )

    # ---- low level -------------------------------------------------------
    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(4):
            r = self.http.request(method, path, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            return r
        return r

    def _post(self, path: str, body: dict) -> dict | None:
        r = self._request("POST", f"/api/{self.acct}/datastorage/{path}/", json=body)
        if r.status_code == 404:  # "not found" style responses
            return None
        if r.status_code >= 400:
            raise ForagerError(f"{path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # ---- companies -------------------------------------------------------
    def org_by_domain(self, domain: str) -> dict | None:
        data = self._post("organization_search", {"domains": [domain]})
        results = (data or {}).get("search_results") or []
        return results[0] if results else None

    def org_by_linkedin(self, slug: str) -> dict | None:
        data = self._post("organization_search", {"linkedin_public_identifiers": [slug]})
        results = (data or {}).get("search_results") or []
        return results[0] if results else None

    # ---- people ----------------------------------------------------------
    def find_people(
        self,
        domain: str,
        include: list[str],
        exclude: list[str],
        limit: int,
        max_pages: int = 3,
    ) -> list[dict]:
        """Current employees of `domain`, filtered by job title.

        Server-side: boolean role_title query. Client-side: strict include/exclude
        check so the filter always holds even if the boolean syntax is loose.
        """
        query = " OR ".join(f'"{t}"' for t in include) if include else None
        found: dict[int, dict] = {}
        for page in range(1, max_pages + 1):
            body: dict[str, Any] = {
                "organization_domains": [domain],
                "role_is_current": True,
                "page": page,
            }
            if query:
                body["role_title"] = query
            data = self._post("person_role_search", body) or {}
            rows = data.get("search_results") or []
            if not rows:
                break
            for row in rows:
                person = row.get("person") or {}
                if not title_matches(row.get("role_title"), include, exclude):
                    continue
                pid = person.get("id")
                if pid and pid not in found:
                    found[pid] = row
            if len(found) >= limit:
                break
        return list(found.values())[:limit]

    def find_person_by_name(self, full_name: str, domain: str) -> dict | None:
        data = self._post(
            "person_role_search",
            {
                "person_name": f'"{full_name}"',
                "organization_domains": [domain],
                "role_is_current": True,
            },
        )
        rows = (data or {}).get("search_results") or []
        return rows[0] if rows else None

    def person_detail(self, person_id: int | None = None, slug: str | None = None) -> dict | None:
        body = {"person_id": person_id} if person_id else {"linkedin_public_identifier": slug}
        return self._post("person_detail_lookup", body)

    def phone(self, person_id: int | None = None, slug: str | None = None) -> str | None:
        body = {"person_id": person_id} if person_id else {"linkedin_public_identifier": slug}
        try:
            data = self._post("person_contacts_lookup/phone_numbers", body)
        except ForagerError as exc:
            log.warning("phone lookup failed: %s", exc)
            return None
        return (data or {}).get("phone_number")

    def work_email(self, slug: str, timeout: int = 90) -> str | None:
        """Async v2 lookup: start job, poll until completed (polling is free)."""
        r = self._request(
            "POST",
            f"/api/v2/{self.acct}/datastorage/person_contacts_lookup/work_emails/",
            json={"linkedin_public_identifier": slug},
        )
        if r.status_code >= 400:
            log.warning("email lookup start failed: %s %s", r.status_code, r.text[:200])
            return None
        status_url = r.json()["status_check_url"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            s = self._request("GET", status_url)
            if s.status_code >= 400:
                return None
            body = s.json()
            if body.get("status") == "failed":
                return None
            if body.get("status") == "completed":
                for item in body.get("emails") or []:
                    email = item.get("email") if isinstance(item, dict) else item
                    if email:
                        return email
                return None
        log.warning("email lookup timed out for %s", slug)
        return None

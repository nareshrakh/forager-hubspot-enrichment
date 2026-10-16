"""Orchestration: the 5 tasks from the brief."""
import logging

from .config import Settings
from .forager import ForagerClient
from .hubspot import HubSpotClient
from .mapping import (
    COMPANY_READ, CONTACT_READ, FREE_MAIL, clean_domain, company_props,
    contact_props, current_role, slug_from_url,
)

log = logging.getLogger(__name__)


class EnrichmentService:
    def __init__(self, settings: Settings, forager: ForagerClient, hubspot: HubSpotClient):
        self.s, self.fg, self.hs = settings, forager, hubspot

    # ---- Task 1: enrich company -----------------------------------------
    def enrich_company(self, company_id: str) -> dict:
        existing = self.hs.get("companies", company_id, COMPANY_READ)["properties"]
        domain = clean_domain(existing.get("domain") or existing.get("website"))
        org = self.fg.org_by_domain(domain) if domain else None
        if not org:
            slug = slug_from_url(existing.get("linkedin_company_page"))
            org = self.fg.org_by_linkedin(slug) if slug else None
        if not org:
            self.hs.update("companies", company_id, {"forager_status": "not_found"})
            return {"company_id": company_id, "status": "not_found"}
        props = company_props(org, existing, self.s.enable_extras)
        self.hs.update("companies", company_id, props)
        return {
            "company_id": company_id, "status": "enriched",
            "domain": clean_domain(org.get("domain")), "name": org.get("name"),
        }

    # ---- Task 2: enrich contact -----------------------------------------
    def enrich_contact(
        self, contact_id: str, company_id: str | None = None,
        forager_person_id: int | None = None, domain: str | None = None,
    ) -> dict:
        existing = self.hs.get("contacts", contact_id, CONTACT_READ)["properties"]

        if not company_id:
            ids = self.hs.associated_ids("contacts", contact_id, "companies")
            company_id = ids[0] if ids else None
        if not domain and company_id:
            c = self.hs.get("companies", company_id, ["domain"])["properties"]
            domain = clean_domain(c.get("domain"))
        if not domain and existing.get("email"):
            d = existing["email"].split("@")[-1].lower()
            domain = None if d in FREE_MAIL else d

        pid = forager_person_id or _int(existing.get("forager_person_id"))
        slug = slug_from_url(existing.get("person_linkedin_url") or existing.get("hs_linkedin_url"))
        if not pid and not slug and domain:
            name = " ".join(filter(None, [existing.get("firstname"), existing.get("lastname")]))
            hit = self.fg.find_person_by_name(name, domain) if name else None
            pid = ((hit or {}).get("person") or {}).get("id")

        detail = self.fg.person_detail(person_id=pid, slug=None if pid else slug) if (pid or slug) else None
        if not detail:
            self.hs.update("contacts", contact_id, {"forager_status": "not_found"})
            return {"contact_id": contact_id, "status": "not_found"}

        pub_slug = (detail.get("linkedin_info") or {}).get("public_identifier") or slug
        phone = self.fg.phone(person_id=detail["id"])
        email = existing.get("email") or (self.fg.work_email(pub_slug) if pub_slug else None)
        role = current_role(detail, domain)

        props = contact_props(detail, role, phone, email, existing, self.s.enable_extras)
        self.hs.update("contacts", contact_id, props)
        if company_id and company_id not in self.hs.associated_ids("contacts", contact_id, "companies"):
            self.hs.associate("contacts", contact_id, "companies", company_id)
        return {
            "contact_id": contact_id, "status": "enriched", "name": detail.get("full_name"),
            "title": props.get("jobtitle"), "email": props.get("email"), "phone": phone,
        }

    # ---- Task 3: find contacts at a company (job-title filtered) --------
    def find_contacts(
        self, company_id: str, domain: str | None = None,
        include: list[str] | None = None, exclude: list[str] | None = None,
        limit: int | None = None, enrich: bool = True,
    ) -> list[dict]:
        include = include if include is not None else self.s.split(self.s.default_include_titles)
        exclude = exclude if exclude is not None else self.s.split(self.s.default_exclude_titles)
        limit = limit or self.s.max_contacts
        if not domain:
            c = self.hs.get("companies", company_id, ["domain"])["properties"]
            domain = clean_domain(c.get("domain"))
        if not domain:
            return []

        # skip people already in HubSpot for this company
        known = set()
        for cid in self.hs.associated_ids("companies", company_id, "contacts"):
            p = self.hs.get("contacts", cid, CONTACT_READ)["properties"]
            known.add(_int(p.get("forager_person_id")))
            known.add(slug_from_url(p.get("person_linkedin_url") or p.get("hs_linkedin_url")))

        out = []
        for row in self.fg.find_people(domain, include, exclude, limit + len(known)):
            person = row["person"]
            slug = (person.get("linkedin_info") or {}).get("public_identifier")
            if person["id"] in known or (slug and slug in known):
                continue
            created = self.hs.create("contacts", {
                "firstname": person.get("first_name"), "lastname": person.get("last_name"),
                "jobtitle": row.get("role_title"),
                "company": (row.get("organization") or {}).get("name"),
                "forager_person_id": str(person["id"]),
                "forager_status": "pending",  # guard: contact webhook skips these
            })
            self.hs.associate("contacts", created["id"], "companies", company_id)
            result = {"contact_id": created["id"], "name": person.get("full_name"), "title": row.get("role_title")}
            if enrich:
                result = self._safe(self.enrich_contact, created["id"], company_id, person["id"], domain) or result
            out.append(result)
            if len(out) >= limit:
                break
        return out

    # ---- Task 4: company webhook ----------------------------------------
    def process_company_created(self, company_id: str) -> None:
        existing = self.hs.get("companies", company_id, COMPANY_READ)["properties"]
        if existing.get("forager_status"):
            return log.info("company %s already processed, skipping", company_id)
        result = self.enrich_company(company_id)
        if result["status"] != "enriched":
            return
        domain = result["domain"]
        contact_ids = self.hs.associated_ids("companies", company_id, "contacts")
        if contact_ids:  # contacts exist -> enrich them
            for cid in contact_ids:
                self._safe(self.enrich_contact, cid, company_id, None, domain)
        else:  # none -> find them
            self.find_contacts(company_id, domain=domain)

    # ---- Task 5: contact webhook ----------------------------------------
    def process_contact_created(self, contact_id: str) -> None:
        existing = self.hs.get("contacts", contact_id, CONTACT_READ)["properties"]
        if existing.get("forager_status"):  # created by us, or already enriched
            return log.info("contact %s already processed, skipping", contact_id)
        self.enrich_contact(contact_id)

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:  # one bad record must not abort the batch
            log.exception("%s failed for %s", fn.__name__, args[:1])
            return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

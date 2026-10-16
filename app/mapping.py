"""Pure functions: Forager payloads -> HubSpot properties."""
import re
from urllib.parse import urlparse

FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me"}

COMPANY_READ = [
    "name", "domain", "website", "linkedin_company_page", "forager_status", "forager_org_id",
]
CONTACT_READ = [
    "firstname", "lastname", "email", "phone", "jobtitle", "company",
    "hs_linkedin_url", "person_linkedin_url", "forager_person_id", "forager_status",
]


def clean_domain(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    host = urlparse(value if "//" in value else f"//{value}").netloc or value
    host = host.removeprefix("www.")
    return host or None


def slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"linkedin\.com/(?:in|company)/([^/?#]+)", url)
    return m.group(1) if m else None


def split_location(loc: dict | None) -> dict:
    """Forager location -> city/state/country (osm place types, then name fallback)."""
    out = {"city": None, "state": None, "country": None}
    loc = loc or {}
    for o in loc.get("osm_locations") or []:
        t = (o.get("place_type") or "").lower()
        if t in ("city", "town", "village", "municipality") and not out["city"]:
            out["city"] = o.get("name")
        elif t in ("state", "province", "region") and not out["state"]:
            out["state"] = o.get("name")
        elif t == "country" and not out["country"]:
            out["country"] = o.get("name")
    parts = [p.strip() for p in (loc.get("name") or "").split(",") if p.strip()]
    if parts:
        out["city"] = out["city"] or parts[0]
        out["country"] = out["country"] or (parts[-1] if len(parts) > 1 else None)
        if len(parts) >= 3:
            out["state"] = out["state"] or parts[1]
    return out


def company_props(org: dict, existing: dict, extras: bool) -> dict:
    addr = (org.get("addresses") or [{}])[0] or {}
    li = org.get("linkedin_info") or {}
    founded = org.get("founded_date")
    props = {
        # never overwrite what the user typed in
        "name": existing.get("name") or org.get("name"),
        "domain": existing.get("domain") or org.get("domain"),
        "description": org.get("description"),
        "annualrevenue": (org.get("finance_info") or {}).get("revenue"),
        "numberofemployees": org.get("employees_amount"),
        "linkedin_company_page": li.get("public_profile_url"),
        "city": addr.get("city"),
        "state": addr.get("state"),
        "country": addr.get("country"),
        "founded_year": founded[:4] if founded else None,
        "forager_org_id": str(org["id"]),
        "forager_status": "enriched",
    }
    if extras:
        props.update(
            forager_industry=(li.get("industry") or {}).get("name"),
            forager_keywords=", ".join(k["name"] for k in org.get("keywords") or []),
            forager_operating_status=org.get("operating_status"),
            forager_founded_date=founded,
            forager_domain_rank=org.get("domain_rank"),
        )
    return props


def current_role(detail: dict, domain: str | None) -> dict | None:
    roles = [r for r in detail.get("roles") or [] if r.get("is_current")]
    if domain:
        for r in roles:
            if clean_domain((r.get("organization") or {}).get("domain")) == domain:
                return r
    return roles[0] if roles else None


def contact_props(
    detail: dict, role: dict | None, phone: str | None, email: str | None,
    existing: dict, extras: bool,
) -> dict:
    org = (role or {}).get("organization") or {}
    loc = split_location(detail.get("location"))
    props = {
        "firstname": detail.get("first_name"),
        "lastname": detail.get("last_name"),
        "jobtitle": (role or {}).get("role_title"),
        "company": org.get("name") or (role or {}).get("organization_name"),
        "employer_domain": org.get("domain"),
        "employer_linkedin_url": (org.get("linkedin_info") or {}).get("public_profile_url"),
        "person_linkedin_url": (detail.get("linkedin_info") or {}).get("public_profile_url"),
        "email": existing.get("email") or email,
        "phone": phone,
        "mobilephone": phone,
        "city": loc["city"],
        "state": loc["state"],
        "country": loc["country"],
        "forager_person_id": str(detail["id"]),
        "forager_status": "enriched",
    }
    if extras:
        edu = "; ".join(
            filter(None, [
                ", ".join(filter(None, [e.get("degree"), e.get("field_of_study"), e.get("school_name")]))
                for e in detail.get("educations") or []
            ])
        )
        props.update(
            forager_headline=detail.get("headline"),
            forager_about=detail.get("description"),
            forager_skills=", ".join(s["name"] for s in detail.get("skills") or []),
            forager_education=edu,
        )
    return props

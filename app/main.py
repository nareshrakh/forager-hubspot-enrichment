import base64
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from .config import Settings, get_settings
from .forager import ForagerClient
from .hubspot import HubSpotClient
from .services import EnrichmentService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
_seen_events: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    hs = HubSpotClient(s.hubspot_token)
    try:
        hs.ensure_properties()
    except Exception:
        log.exception("could not ensure custom properties")
    fg = ForagerClient(s.forager_api_key, s.forager_account_id, s.forager_base_url)
    app.state.svc = EnrichmentService(s, fg, hs)
    yield


app = FastAPI(title="Forager x HubSpot enrichment", lifespan=lifespan)


def svc(request: Request) -> EnrichmentService:
    return request.app.state.svc


def require_secret(x_api_secret: str = Header(default=""), s: Settings = Depends(get_settings)):
    if not s.api_secret or not hmac.compare_digest(x_api_secret, s.api_secret):
        raise HTTPException(401, "invalid API secret")


def verify_hubspot_signature(request: Request, body: bytes, s: Settings) -> None:
    """HubSpot v3 signature: HMAC-SHA256(secret, method + url + body + timestamp)."""
    if not s.hubspot_client_secret:
        log.warning("HUBSPOT_CLIENT_SECRET not set - skipping signature check")
        return
    ts = request.headers.get("X-HubSpot-Request-Timestamp", "")
    sig = request.headers.get("X-HubSpot-Signature-v3", "")
    if not ts or abs(time.time() * 1000 - int(ts)) > 300_000:
        raise HTTPException(401, "stale or missing timestamp")
    url = f"{s.public_base_url.rstrip('/')}{request.url.path}"
    msg = f"{request.method}{url}{body.decode()}{ts}".encode()
    expected = base64.b64encode(hmac.new(s.hubspot_client_secret.encode(), msg, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "bad signature")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- Webhooks (Tasks 4 & 5) --------------------------------------------------
@app.post("/webhooks/hubspot")
async def hubspot_webhook(
    request: Request, background: BackgroundTasks, s: Settings = Depends(get_settings),
):
    body = await request.body()
    verify_hubspot_signature(request, body, s)
    handled = 0
    for ev in json.loads(body):
        key = str(ev.get("eventId") or f"{ev.get('subscriptionType')}:{ev.get('objectId')}")
        if key in _seen_events:
            continue
        _seen_events.add(key)
        oid = str(ev["objectId"])
        if ev.get("subscriptionType") == "company.creation":
            background.add_task(svc(request).process_company_created, oid)
            handled += 1
        elif ev.get("subscriptionType") == "contact.creation":
            background.add_task(svc(request).process_contact_created, oid)
            handled += 1
    return {"accepted": handled}  # respond fast; work runs in the background


# ---- Manual endpoints (Tasks 1-3), used by the demo ---------------------------
class CompanyReq(BaseModel):
    company_id: str


class ContactReq(BaseModel):
    contact_id: str


class FindReq(BaseModel):
    company_id: str
    include_titles: list[str] | None = None
    exclude_titles: list[str] | None = None
    limit: int | None = None
    enrich: bool = True


@app.post("/enrich/company", dependencies=[Depends(require_secret)])
def enrich_company(req: CompanyReq, service: EnrichmentService = Depends(svc)):
    return service.enrich_company(req.company_id)


@app.post("/enrich/contact", dependencies=[Depends(require_secret)])
def enrich_contact(req: ContactReq, service: EnrichmentService = Depends(svc)):
    return service.enrich_contact(req.contact_id)


@app.post("/find-contacts", dependencies=[Depends(require_secret)])
def find_contacts(req: FindReq, service: EnrichmentService = Depends(svc)):
    return service.find_contacts(
        req.company_id, include=req.include_titles, exclude=req.exclude_titles,
        limit=req.limit, enrich=req.enrich,
    )

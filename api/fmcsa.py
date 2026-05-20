"""FMCSA QCMobile API proxy.

Real endpoint (when FMCSA_WEB_KEY is set):
    GET https://mobile.fmcsa.dot.gov/qc/services/carriers/docket-number/{mc}?webKey={key}

When the key is unset we run a deterministic mock so demos and local dev work
without depending on a live external API.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .config import settings

FMCSA_BASE = "https://mobile.fmcsa.dot.gov/qc/services/carriers/docket-number"


def _normalize_mc(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _mock_lookup(mc: str) -> dict[str, Any]:
    """Deterministic mock so the same MC always returns the same answer.

    Even MCs are eligible, odd MCs are not authorized, MCs ending in 00 are 'not found'.
    """
    if not mc:
        return {"found": False, "eligible": False, "reason": "Missing MC number"}
    if mc.endswith("00"):
        return {"found": False, "eligible": False, "reason": "MC number not found in FMCSA records"}
    eligible = int(mc) % 2 == 0
    return {
        "found": True,
        "eligible": eligible,
        "mc_number": mc,
        "carrier_name": f"Mock Carrier {mc}",
        "allowed_to_operate": "Y" if eligible else "N",
        "out_of_service": not eligible,
        "reason": None if eligible else "Carrier is currently out of service or not authorized",
        "source": "mock",
    }


async def verify_mc(raw_mc: str) -> dict[str, Any]:
    mc = _normalize_mc(raw_mc)
    if not settings.fmcsa_web_key:
        return _mock_lookup(mc)

    url = f"{FMCSA_BASE}/{mc}"
    params = {"webKey": settings.fmcsa_web_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        return {
            "found": False,
            "eligible": False,
            "reason": f"FMCSA API returned {resp.status_code}",
            "source": "fmcsa",
        }

    payload = resp.json()
    content = payload.get("content") if isinstance(payload, dict) else None
    if not content:
        return {"found": False, "eligible": False, "reason": "MC number not found", "source": "fmcsa"}

    # The QCMobile API returns a list under content; each item has a `carrier` dict.
    record = content[0] if isinstance(content, list) else content
    carrier = record.get("carrier", {}) if isinstance(record, dict) else {}
    allowed = carrier.get("allowedToOperate", "N")
    out_of_service = bool(carrier.get("oosDate"))
    eligible = allowed == "Y" and not out_of_service

    return {
        "found": True,
        "eligible": eligible,
        "mc_number": mc,
        "carrier_name": carrier.get("legalName") or carrier.get("dbaName"),
        "allowed_to_operate": allowed,
        "out_of_service": out_of_service,
        "reason": None if eligible else "Carrier is not authorized or is out of service",
        "source": "fmcsa",
    }

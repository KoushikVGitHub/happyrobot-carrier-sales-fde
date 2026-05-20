"""End-to-end smoke test for /v1/negotiate, outcome enum, and call ingest.

Uses a temp SQLite file so the dev DB is untouched. Prints PASS / FAIL per case.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

tmp_db = Path(tempfile.gettempdir()) / f"smoke_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.as_posix()}"
os.environ["API_KEY"] = "test-key"
os.environ["FMCSA_WEB_KEY"] = ""  # force mock for offline test

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

# TestClient must be a context manager to trigger FastAPI lifespan (which runs init_db + seed).
_ctx = TestClient(app)
client = _ctx.__enter__()
H = {"X-API-Key": "test-key"}

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(name)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


section("Health + auth")
r = client.get("/health")
check("health 200", r.status_code == 200)
r = client.get("/v1/loads")
check("/v1/loads requires key (401)", r.status_code == 401)
r = client.get("/v1/loads", headers=H)
check("/v1/loads with key returns list", r.status_code == 200 and isinstance(r.json(), list))


section("FMCSA mock fallback")
r = client.get("/v1/carriers/verify/123456", headers=H)
body = r.json()
check("verify even MC -> eligible", r.status_code == 200 and body["eligible"] is True and body["source"] == "mock")
r = client.get("/v1/carriers/verify/123457", headers=H)
check("verify odd MC -> not eligible", r.json()["eligible"] is False)
r = client.get("/v1/carriers/verify/123400", headers=H)
check("verify MC ending in 00 -> not found", r.json()["found"] is False)


section("Load search")
r = client.get("/v1/loads/search", params={"origin": "Chicago"}, headers=H)
loads = r.json()
check("search by origin returns rows", r.status_code == 200 and len(loads) > 0)
load_id = loads[0]["load_id"]
loadboard = loads[0]["loadboard_rate"]
print(f"  (using load {load_id} with loadboard ${loadboard:.2f}; ceiling ${loadboard*1.10:.2f})")


section("Negotiate: accept-first-offer below 1.05x")
call_id_a = f"smoke-{uuid.uuid4().hex[:8]}"
offer = round(loadboard * 1.03, 2)
r = client.post("/v1/negotiate", headers=H, json={"call_id": call_id_a, "load_id": load_id, "carrier_offer": offer})
b = r.json()
check("round 1 accept", b["action"] == "accept" and b["round"] == 1 and b["agreed_rate"] == offer)


section("Negotiate: counter twice, then accept at round 2 cap")
call_id_b = f"smoke-{uuid.uuid4().hex[:8]}"
r1 = client.post("/v1/negotiate", headers=H, json={"call_id": call_id_b, "load_id": load_id, "carrier_offer": loadboard * 1.20}).json()
check("round 1 counter at 1.05x", r1["action"] == "counter" and r1["round"] == 1 and abs(r1["counter_rate"] - round(loadboard*1.05,2)) < 0.01)
offer2 = round(loadboard * 1.07, 2)
r2 = client.post("/v1/negotiate", headers=H, json={"call_id": call_id_b, "load_id": load_id, "carrier_offer": offer2}).json()
check("round 2 accept (offer within 1.08x)", r2["action"] == "accept" and r2["round"] == 2 and r2["agreed_rate"] == offer2)


section("Negotiate: push past ceiling -> end on round 3")
call_id_c = f"smoke-{uuid.uuid4().hex[:8]}"
for n in range(1, 3):
    rN = client.post("/v1/negotiate", headers=H, json={"call_id": call_id_c, "load_id": load_id, "carrier_offer": loadboard * 1.50}).json()
    check(f"round {n} counter", rN["action"] == "counter" and rN["round"] == n)
r3 = client.post("/v1/negotiate", headers=H, json={"call_id": call_id_c, "load_id": load_id, "carrier_offer": loadboard * 1.50}).json()
check("round 3 end (above ceiling)", r3["action"] == "end" and r3["round"] == 3 and r3["agreed_rate"] is None)
r4 = client.post("/v1/negotiate", headers=H, json={"call_id": call_id_c, "load_id": load_id, "carrier_offer": loadboard}).json()
check("round 4+ guard returns end", r4["action"] == "end")


section("Negotiate: unknown load -> 404")
r = client.post("/v1/negotiate", headers=H, json={"call_id": "x", "load_id": "DOES-NOT-EXIST", "carrier_offer": 1000})
check("404 for missing load", r.status_code == 404)


section("Call ingest with widened outcome enum")
for outcome in ["booked", "ineligible", "declined", "system_error", "no_match", "negotiation_failed", "abandoned"]:
    payload = {"call_id": f"call-{outcome}", "mc_number": "123456", "outcome": outcome, "sentiment": "neutral",
               "load_id": load_id if outcome == "booked" else None,
               "agreed_rate": offer if outcome == "booked" else None, "rounds": 1}
    r = client.post("/v1/calls", headers=H, json=payload)
    check(f"POST /v1/calls accepts outcome={outcome}", r.status_code == 201, detail=r.text)

r = client.post("/v1/calls", headers=H, json={"call_id": "legacy-1", "outcome": "carrier_not_eligible", "sentiment": "neutral"})
check("unknown outcome coerced to system_error (201)", r.status_code == 201 and r.json()["outcome"] == "system_error")


section("Metrics + booked-load flip")
r = client.get(f"/v1/loads/{load_id}", headers=H).json()
check("booked load flagged after POST /v1/calls outcome=booked", r["booked"] is True)
m = client.get("/v1/metrics/summary", headers=H).json()
check("metrics summary returns total_calls > 0", m["total_calls"] > 0)


print(f"\n{'='*50}")
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")

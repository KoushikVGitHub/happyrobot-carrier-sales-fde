from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from sqlmodel import Session, select, func

from .auth import require_api_key
from .db import engine, get_session, init_db
from .fmcsa import verify_mc
from .models import Call, Load, NegotiationRound
from .schemas import CallIn, LoadIn, MetricsSummary, NegotiateIn, NegotiateOut


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="HappyRobot Inbound Carrier Sales API",
    version="1.0.0",
    description=(
        "Backs the HappyRobot inbound voice agent. Exposes load search, "
        "FMCSA carrier verification, and a call-event sink for the dashboard."
    ),
    lifespan=lifespan,
)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "HappyRobot Inbound Carrier Sales API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# ---------- Carriers (FMCSA) ----------

@app.get("/v1/carriers/verify/{mc_number}", tags=["carriers"], dependencies=[Depends(require_api_key)])
async def verify_carrier(mc_number: str) -> dict:
    return await verify_mc(mc_number)


@app.get("/v1/carriers/verify", tags=["carriers"], dependencies=[Depends(require_api_key)])
async def verify_carrier_query(
    mc: Optional[str] = Query(None, description="MC number, digits only"),
    mc_number: Optional[str] = Query(None, description="Alias for mc"),
) -> dict:
    value = mc or mc_number
    if not value:
        raise HTTPException(400, "Provide ?mc=<number> (or ?mc_number=...)")
    return await verify_mc(value)


# ---------- Loads ----------

@app.get("/v1/loads/search", tags=["loads"], dependencies=[Depends(require_api_key)])
def search_loads(
    origin: Optional[str] = Query(None, description="Case-insensitive substring match on origin"),
    destination: Optional[str] = Query(None, description="Case-insensitive substring match on destination"),
    equipment_type: Optional[str] = Query(None),
    pickup_date: Optional[str] = Query(None, description="YYYY-MM-DD; matches pickups on this date"),
    min_rate: Optional[float] = Query(None),
    max_rate: Optional[float] = Query(None),
    include_booked: bool = Query(False),
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_session),
) -> list[Load]:
    stmt = select(Load)
    if origin:
        stmt = stmt.where(func.lower(Load.origin).contains(origin.lower()))
    if destination:
        stmt = stmt.where(func.lower(Load.destination).contains(destination.lower()))
    if equipment_type:
        stmt = stmt.where(func.lower(Load.equipment_type) == equipment_type.lower())
    if pickup_date:
        try:
            day = datetime.fromisoformat(pickup_date).date()
        except ValueError:
            raise HTTPException(400, "pickup_date must be YYYY-MM-DD")
        start = datetime.combine(day, datetime.min.time())
        end = datetime.combine(day, datetime.max.time())
        stmt = stmt.where(Load.pickup_datetime >= start, Load.pickup_datetime <= end)
    if min_rate is not None:
        stmt = stmt.where(Load.loadboard_rate >= min_rate)
    if max_rate is not None:
        stmt = stmt.where(Load.loadboard_rate <= max_rate)
    if not include_booked:
        stmt = stmt.where(Load.booked == False)  # noqa: E712
    stmt = stmt.order_by(Load.pickup_datetime).limit(limit)
    return list(session.exec(stmt))


@app.get("/v1/loads", tags=["loads"], dependencies=[Depends(require_api_key)])
def list_loads(session: Session = Depends(get_session)) -> list[Load]:
    return list(session.exec(select(Load).order_by(Load.pickup_datetime)))


@app.get("/v1/loads/{load_id}", tags=["loads"], dependencies=[Depends(require_api_key)])
def get_load(load_id: str, session: Session = Depends(get_session)) -> Load:
    load = session.get(Load, load_id)
    if not load:
        raise HTTPException(404, f"Load {load_id} not found")
    return load


@app.post("/v1/loads/{load_id}/unbook", tags=["loads"], dependencies=[Depends(require_api_key)])
def unbook_load(load_id: str, session: Session = Depends(get_session)) -> Load:
    load = session.get(Load, load_id)
    if not load:
        raise HTTPException(404, f"Load {load_id} not found")
    load.booked = False
    session.add(load)
    session.commit()
    session.refresh(load)
    return load


@app.post("/v1/loads", tags=["loads"], status_code=201, dependencies=[Depends(require_api_key)])
def create_load(payload: LoadIn, session: Session = Depends(get_session)) -> Load:
    if session.get(Load, payload.load_id):
        raise HTTPException(409, f"Load {payload.load_id} already exists")
    load = Load(**payload.model_dump())
    session.add(load)
    session.commit()
    session.refresh(load)
    return load


# ---------- Negotiation ----------

ROUND_CAPS = {1: 1.05, 2: 1.08, 3: 1.10}
CEILING_MULTIPLIER = 1.10
MAX_ROUNDS = 3


@app.post("/v1/negotiate", tags=["negotiation"], response_model=NegotiateOut, dependencies=[Depends(require_api_key)])
def negotiate_rate(
    body: dict = Body(default_factory=dict),
    call_id: Optional[str] = Query(None),
    load_id: Optional[str] = Query(None),
    carrier_offer: Optional[float] = Query(None),
    session: Session = Depends(get_session),
) -> NegotiateOut:
    def pick(name: str):
        v = body.get(name) if isinstance(body, dict) else None
        if v in (None, ""):
            v = {"call_id": call_id, "load_id": load_id, "carrier_offer": carrier_offer}[name]
        return v

    cid, lid, off = pick("call_id"), pick("load_id"), pick("carrier_offer")
    if not (cid and lid and off not in (None, "")):
        raise HTTPException(422, "Need call_id, load_id, carrier_offer (body or query params)")
    try:
        off = float(off)
    except (TypeError, ValueError):
        raise HTTPException(422, f"carrier_offer not numeric: {off!r}")
    return _negotiate_core(NegotiateIn(call_id=str(cid), load_id=str(lid), carrier_offer=off), session)


def _negotiate_core(payload: NegotiateIn, session: Session) -> NegotiateOut:
    load = session.get(Load, payload.load_id)
    if not load:
        raise HTTPException(404, f"Load {payload.load_id} not found")

    loadboard = load.loadboard_rate
    ceiling = round(loadboard * CEILING_MULTIPLIER, 2)

    prior = session.exec(
        select(func.count()).select_from(NegotiationRound)
        .where(NegotiationRound.call_id == payload.call_id, NegotiationRound.load_id == payload.load_id)
    ).one()
    current_round = int(prior) + 1

    if current_round > MAX_ROUNDS:
        return NegotiateOut(
            round=MAX_ROUNDS,
            action="end",
            loadboard_rate=loadboard,
            ceiling=ceiling,
            reason="Negotiation already exhausted 3 rounds.",
        )

    cap_amount = round(loadboard * ROUND_CAPS[current_round], 2)

    if payload.carrier_offer <= cap_amount:
        record = NegotiationRound(
            call_id=payload.call_id, load_id=payload.load_id, round=current_round,
            carrier_offer=payload.carrier_offer, agent_counter=payload.carrier_offer, accepted=True,
        )
        session.add(record)
        session.commit()
        return NegotiateOut(
            round=current_round, action="accept",
            agreed_rate=payload.carrier_offer,
            loadboard_rate=loadboard, ceiling=ceiling,
            reason=f"Carrier offer within round-{current_round} cap (${cap_amount:.2f}).",
        )

    if current_round == MAX_ROUNDS:
        record = NegotiationRound(
            call_id=payload.call_id, load_id=payload.load_id, round=current_round,
            carrier_offer=payload.carrier_offer, agent_counter=None, accepted=False,
        )
        session.add(record)
        session.commit()
        return NegotiateOut(
            round=current_round, action="end",
            loadboard_rate=loadboard, ceiling=ceiling,
            reason=f"Carrier offer ${payload.carrier_offer:.2f} exceeds ceiling ${ceiling:.2f} on final round.",
        )

    record = NegotiationRound(
        call_id=payload.call_id, load_id=payload.load_id, round=current_round,
        carrier_offer=payload.carrier_offer, agent_counter=cap_amount, accepted=False,
    )
    session.add(record)
    session.commit()
    return NegotiateOut(
        round=current_round, action="counter",
        counter_rate=cap_amount,
        loadboard_rate=loadboard, ceiling=ceiling,
        reason=f"Round {current_round} counter at {int(ROUND_CAPS[current_round]*100)}% of loadboard rate.",
    )


# ---------- HappyRobot signal dispatcher ----------
# Single webhook that receives all tool invocations from the HappyRobot agent.
# Permissive: accepts multiple payload shapes, logs everything, dispatches by tool name.

def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


@app.post("/v1/signals", tags=["signals"], dependencies=[Depends(require_api_key)])
async def signals_dispatch(
    payload: dict = Body(default_factory=dict),
    session: Session = Depends(get_session),
) -> dict:
    print(f"[SIGNAL] received: {payload}", flush=True)

    tool_raw = _first(payload, "tool_name", "tool", "name", "event", "action", default="")
    args = _first(payload, "parameters", "params", "arguments", "args", "data", "input", "payload", default={}) or {}
    if not isinstance(args, dict):
        args = {"value": args}
    # Some platforms put the args at the top level alongside tool_name
    merged_args = {**{k: v for k, v in payload.items() if k not in {"tool_name","tool","name","event","action","parameters","params","arguments","args","data","input","payload"}}, **args}
    tool = str(tool_raw).strip().lower().replace("-", "_")

    if not tool:
        return {"ok": False, "error": "no tool name in payload", "echo": payload}

    if tool in {"verify_carrier", "verifycarrier", "carrier_verify"}:
        mc = _first(merged_args, "mc_number", "mc", "mcnumber", "carrier_mc")
        if not mc:
            return {"ok": False, "tool": tool, "error": "missing mc_number"}
        result = await verify_mc(str(mc))
        return {"ok": True, "tool": tool, "result": result}

    if tool in {"search_loads", "searchloads", "find_loads"}:
        origin = _first(merged_args, "origin", "pickup", "from")
        destination = _first(merged_args, "destination", "dropoff", "to")
        equipment_type = _first(merged_args, "equipment_type", "equipment", "trailer")
        stmt = select(Load).where(Load.booked == False)  # noqa: E712
        if origin:
            stmt = stmt.where(func.lower(Load.origin).contains(str(origin).lower()))
        if destination:
            stmt = stmt.where(func.lower(Load.destination).contains(str(destination).lower()))
        if equipment_type:
            stmt = stmt.where(func.lower(Load.equipment_type) == str(equipment_type).lower())
        stmt = stmt.order_by(Load.pickup_datetime).limit(5)
        rows = [r.model_dump(mode="json") for r in session.exec(stmt)]
        return {"ok": True, "tool": tool, "result": {"count": len(rows), "loads": rows}}

    if tool in {"negotiate_rate", "negotiaterate", "negotiate"}:
        call_id = _first(merged_args, "call_id", "callid", "call", "conversation_id")
        load_id = _first(merged_args, "load_id", "loadid", "load")
        offer = _first(merged_args, "carrier_offer", "offer", "rate", "amount", "price")
        if not (call_id and load_id and offer is not None):
            return {"ok": False, "tool": tool, "error": "need call_id, load_id, carrier_offer", "got": merged_args}
        try:
            offer_f = float(offer)
        except (TypeError, ValueError):
            return {"ok": False, "tool": tool, "error": "carrier_offer not numeric"}
        result = _negotiate_core(NegotiateIn(call_id=str(call_id), load_id=str(load_id), carrier_offer=offer_f), session)
        return {"ok": True, "tool": tool, "result": result.model_dump(mode="json")}

    if tool in {"mock_transfer", "mocktransfer", "transfer"}:
        return {"ok": True, "tool": tool, "result": {"transferred": True, "message": "Transfer successful. You can wrap up the conversation."}}

    return {"ok": False, "tool": tool, "error": "unknown tool", "echo": payload}


# ---------- Calls ----------

_VALID_OUTCOMES = {"booked", "negotiation_failed", "no_match", "ineligible", "declined", "system_error", "abandoned"}
_VALID_SENTIMENTS = {"positive", "neutral", "negative"}


def _coerce_call_payload(raw: dict) -> CallIn:
    """Strip empty strings, default missing/unknown outcome+sentiment, coerce numerics.

    Accepts alias field names from HappyRobot's Classify node:
        - `negotiation_rounds` → `rounds`
        - `classification` → `sentiment` (if value matches a sentiment class) or `outcome`
    """
    cleaned: dict = {}
    for k, v in (raw or {}).items():
        if v in (None, "") or (isinstance(v, str) and v.strip().lower() in {"null", "none", "n/a", "nan"}):
            continue
        cleaned[k] = v
    if "negotiation_rounds" in cleaned and "rounds" not in cleaned:
        cleaned["rounds"] = cleaned.pop("negotiation_rounds")
    if "classification" in cleaned:
        val = str(cleaned.pop("classification")).strip().lower()
        if val in _VALID_SENTIMENTS and "sentiment" not in cleaned:
            cleaned["sentiment"] = val
        elif val in _VALID_OUTCOMES and "outcome" not in cleaned:
            cleaned["outcome"] = val
    for nk in ("initial_offer", "agreed_rate"):
        if nk in cleaned:
            try:
                cleaned[nk] = float(cleaned[nk])
            except (TypeError, ValueError):
                cleaned.pop(nk, None)
    if "rounds" in cleaned:
        try:
            cleaned["rounds"] = int(float(cleaned["rounds"]))
        except (TypeError, ValueError):
            cleaned.pop(nk, None)
    outcome = str(cleaned.get("outcome", "")).strip().lower().replace(" ", "_")
    cleaned["outcome"] = outcome if outcome in _VALID_OUTCOMES else "system_error"
    sent = str(cleaned.get("sentiment", "")).strip().lower()
    cleaned["sentiment"] = sent if sent in _VALID_SENTIMENTS else "neutral"
    return CallIn(**cleaned)


@app.post("/v1/calls", tags=["calls"], status_code=201, dependencies=[Depends(require_api_key)])
def log_call(raw: dict = Body(default_factory=dict), session: Session = Depends(get_session)) -> Call:
    print(f"[CALL_INGEST] {raw}", flush=True)
    payload = _coerce_call_payload(raw)
    # Idempotent: if a call with this call_id exists, update it instead of failing on UNIQUE.
    existing = session.get(Call, payload.call_id) if payload.call_id else None
    if existing:
        for field in ("mc_number", "carrier_name", "load_id", "initial_offer", "agreed_rate", "rounds", "outcome", "sentiment", "notes", "transcript", "ended_at"):
            v = getattr(payload, field, None)
            if v not in (None, "", 0):
                setattr(existing, field, v)
        if payload.outcome == "booked" and existing.load_id:
            load = session.get(Load, existing.load_id)
            if load:
                load.booked = True
                session.add(load)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    call = Call(
        call_id=payload.call_id or str(uuid.uuid4()),
        mc_number=payload.mc_number,
        carrier_name=payload.carrier_name,
        load_id=payload.load_id,
        initial_offer=payload.initial_offer,
        agreed_rate=payload.agreed_rate,
        rounds=payload.rounds,
        outcome=payload.outcome,
        sentiment=payload.sentiment,
        notes=payload.notes,
        transcript=payload.transcript,
        started_at=payload.started_at or datetime.utcnow(),
        ended_at=payload.ended_at,
    )
    session.add(call)
    # When a call is booked, mark the matching load as booked so it stops showing up in search.
    if payload.outcome == "booked" and payload.load_id:
        load = session.get(Load, payload.load_id)
        if load:
            load.booked = True
            session.add(load)
    session.commit()
    session.refresh(call)
    return call


@app.get("/v1/calls", tags=["calls"], dependencies=[Depends(require_api_key)])
def list_calls(
    limit: int = Query(200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[Call]:
    return list(session.exec(select(Call).order_by(Call.started_at.desc()).limit(limit)))


@app.delete("/v1/calls/{call_id}", tags=["calls"], dependencies=[Depends(require_api_key)])
def delete_call(call_id: str, session: Session = Depends(get_session)) -> dict:
    obj = session.get(Call, call_id)
    if not obj:
        raise HTTPException(404, f"Call {call_id} not found")
    session.delete(obj)
    session.commit()
    return {"deleted": call_id}


@app.delete("/v1/calls", tags=["calls"], dependencies=[Depends(require_api_key)])
def delete_all_calls(
    confirm: Optional[str] = Query(None, description="Pass 'yes' to wipe all call records"),
    session: Session = Depends(get_session),
) -> dict:
    if confirm != "yes":
        raise HTTPException(400, "Pass ?confirm=yes to wipe all calls")
    rows = list(session.exec(select(Call)))
    for r in rows:
        session.delete(r)
    session.commit()
    return {"deleted": len(rows)}


# ---------- Metrics ----------

@app.get("/v1/metrics/summary", tags=["metrics"], response_model=MetricsSummary, dependencies=[Depends(require_api_key)])
def metrics_summary(session: Session = Depends(get_session)) -> MetricsSummary:
    calls = list(session.exec(select(Call)))
    total = len(calls)
    booked = sum(1 for c in calls if c.outcome == "booked")
    agreed = [c.agreed_rate for c in calls if c.agreed_rate is not None]
    avg_agreed = round(sum(agreed) / len(agreed), 2) if agreed else None
    avg_rounds = round(sum(c.rounds for c in calls) / total, 2) if total else 0.0

    outcomes: dict[str, int] = {}
    sentiments: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for c in calls:
        outcomes[c.outcome] = outcomes.get(c.outcome, 0) + 1
        sentiments[c.sentiment] = sentiments.get(c.sentiment, 0) + 1
        day = c.started_at.date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1

    calls_per_day = [{"date": d, "count": n} for d, n in sorted(by_day.items())]
    return MetricsSummary(
        total_calls=total,
        booked=booked,
        booking_rate=round(booked / total, 3) if total else 0.0,
        avg_agreed_rate=avg_agreed,
        avg_rounds=avg_rounds,
        outcomes=outcomes,
        sentiments=sentiments,
        calls_per_day=calls_per_day,
    )

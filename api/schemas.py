from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Outcome = Literal[
    "booked",
    "negotiation_failed",
    "no_match",
    "ineligible",
    "declined",
    "system_error",
    "abandoned",
]
Sentiment = Literal["positive", "neutral", "negative"]


class NegotiateIn(BaseModel):
    """Payload from the HappyRobot agent's `negotiate_rate` tool."""
    call_id: str
    load_id: str
    carrier_offer: float = Field(gt=0)


class NegotiateOut(BaseModel):
    round: int
    action: Literal["accept", "counter", "end"]
    counter_rate: Optional[float] = None
    agreed_rate: Optional[float] = None
    loadboard_rate: float
    ceiling: float
    reason: Optional[str] = None


class CallIn(BaseModel):
    """Payload the HappyRobot workflow POSTs at end-of-call via the Extract node."""
    call_id: Optional[str] = None
    mc_number: Optional[str] = None
    carrier_name: Optional[str] = None
    load_id: Optional[str] = None
    initial_offer: Optional[float] = None
    agreed_rate: Optional[float] = None
    rounds: int = 0
    outcome: Outcome
    sentiment: Sentiment
    notes: Optional[str] = None
    transcript: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class LoadIn(BaseModel):
    load_id: str
    origin: str
    destination: str
    pickup_datetime: datetime
    delivery_datetime: datetime
    equipment_type: str
    loadboard_rate: float
    weight: int
    commodity_type: str
    num_of_pieces: int
    miles: int
    notes: Optional[str] = None
    dimensions: Optional[str] = None


class MetricsSummary(BaseModel):
    total_calls: int
    booked: int
    booking_rate: float
    avg_agreed_rate: Optional[float]
    avg_rounds: float
    outcomes: dict[str, int]
    sentiments: dict[str, int]
    calls_per_day: list[dict] = Field(default_factory=list)

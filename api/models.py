from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Load(SQLModel, table=True):
    load_id: str = Field(primary_key=True)
    origin: str
    destination: str
    pickup_datetime: datetime
    delivery_datetime: datetime
    equipment_type: str
    loadboard_rate: float
    notes: Optional[str] = None
    weight: int
    commodity_type: str
    num_of_pieces: int
    miles: int
    dimensions: Optional[str] = None
    booked: bool = Field(default=False, index=True)


class Call(SQLModel, table=True):
    call_id: str = Field(primary_key=True)
    mc_number: Optional[str] = Field(default=None, index=True)
    carrier_name: Optional[str] = None
    load_id: Optional[str] = Field(default=None, index=True)
    initial_offer: Optional[float] = None
    agreed_rate: Optional[float] = None
    rounds: int = 0
    outcome: str = Field(index=True)
    sentiment: str = Field(index=True)
    notes: Optional[str] = None
    transcript: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    ended_at: Optional[datetime] = None


class NegotiationRound(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    call_id: str = Field(index=True)
    load_id: str = Field(index=True)
    round: int
    carrier_offer: float
    agent_counter: Optional[float] = None
    accepted: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

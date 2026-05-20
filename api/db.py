import json
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .models import Load

_DATETIME_FIELDS = ("pickup_datetime", "delivery_datetime")

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _seed_loads_if_empty()


def _seed_loads_if_empty() -> None:
    seed_path = Path(__file__).parent / "seed_loads.json"
    if not seed_path.exists():
        return
    with Session(engine) as session:
        existing = session.exec(select(Load).limit(1)).first()
        if existing:
            return
        rows = json.loads(seed_path.read_text())
        for row in rows:
            for f in _DATETIME_FIELDS:
                if isinstance(row.get(f), str):
                    row[f] = datetime.fromisoformat(row[f])
            session.add(Load(**row))
        session.commit()


def get_session():
    with Session(engine) as session:
        yield session

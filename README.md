# Inbound Carrier Sales — HappyRobot FDE Take-Home

A working POC of an automated inbound carrier-sales agent for a freight broker. A carrier calls in (inbound phone trigger on the HappyRobot platform), the agent vets them via FMCSA, pitches a matching load, negotiates up to three rounds with a defined rate ceiling, mocks a transfer to a sales rep, and logs the call. An operator dashboard surfaces volume, conversion, pricing, and sentiment.

This repo contains everything except the HappyRobot workflow itself (built on platform).

## 🚀 Live demo

| Service | URL |
|---|---|
| API | <https://happyrobot-carrier-api-kv.fly.dev> |
| API docs (OpenAPI/Swagger) | <https://happyrobot-carrier-api-kv.fly.dev/docs> |
| Dashboard | <https://happyrobot-carrier-dashboard-kv.fly.dev> |
| Health check | <https://happyrobot-carrier-api-kv.fly.dev/health> |

All `/v1/*` routes require `X-API-Key`. The key for the reviewer is shared in the submission email — not in this repo.

## Architecture

```
   ┌─────────────────────┐        ┌──────────────────────────────┐
   │ HappyRobot workflow │──HTTP──▶ FastAPI (loads, FMCSA, calls) │──┐
   │   (web call agent)  │        └──────────────────────────────┘  │
   └─────────────────────┘                    ▲                     │
                                              │ X-API-Key            ▼
                                  ┌─────────────────────┐    ┌──────────────┐
                                  │ Streamlit dashboard │◀───│   SQLite     │
                                  └─────────────────────┘    └──────────────┘
```

- **FastAPI** — `api/` — load search, FMCSA carrier verification, call ingest, metrics. All routes require `X-API-Key`.
- **Streamlit** — `dashboard/` — KPIs, outcome + sentiment charts, recent-calls table. Reads through the same API.
- **SQLite** — single file on a persistent volume. Seeded with 8 sample loads on first boot.
- **FMCSA** — real QCMobile API when `FMCSA_WEB_KEY` is set, deterministic mock otherwise (so demos work offline).

## Quick start (local, with Docker)

```bash
cp .env.example .env
# edit .env: set API_KEY to something long & random, optionally FMCSA_WEB_KEY
docker compose up --build
```

- API → http://localhost:8000 (OpenAPI docs at `/docs`)
- Dashboard → http://localhost:8501

Health check:

```bash
curl http://localhost:8000/health
```

A protected call (note the header):

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/v1/loads/search?origin=Chicago&equipment_type=Dry%20Van"
```

## Quick start (local, no Docker)

```bash
python -m venv .venv && .venv\Scripts\activate   # or source .venv/bin/activate
pip install -r api/requirements.txt -r dashboard/requirements.txt
cp .env.example .env

# Terminal 1
uvicorn api.main:app --reload

# Terminal 2
$env:API_BASE_URL = "http://localhost:8000"   # PowerShell. bash: export API_BASE_URL=...
streamlit run dashboard/app.py
```

## Endpoint reference

All `/v1/*` endpoints require `X-API-Key: <your key>`.

| Method | Path                              | Purpose |
|--------|-----------------------------------|---------|
| GET    | `/health`                         | Liveness, no auth |
| GET    | `/v1/carriers/verify/{mc_number}` | FMCSA eligibility lookup |
| GET    | `/v1/loads/search`                | Filter loads by origin/destination/equipment/pickup_date/rate |
| GET    | `/v1/loads`                       | List all loads |
| GET    | `/v1/loads/{load_id}`             | Get one |
| POST   | `/v1/loads`                       | Create a load |
| POST   | `/v1/negotiate`                   | Score a carrier counter-offer (server tracks rounds) |
| POST   | `/v1/calls`                       | Ingest a call event (end-of-call) |
| GET    | `/v1/calls`                       | Recent calls (for dashboard) |
| GET    | `/v1/metrics/summary`             | Aggregated metrics |

### Search example
```
GET /v1/loads/search?origin=Chicago&destination=Dallas&equipment_type=Dry%20Van&pickup_date=2026-05-16
```

### FMCSA verify response
```json
{
  "found": true,
  "eligible": true,
  "mc_number": "123456",
  "carrier_name": "Mock Carrier 123456",
  "allowed_to_operate": "Y",
  "out_of_service": false,
  "reason": null,
  "source": "mock"
}
```

### Call ingest payload
```json
{
  "mc_number": "123456",
  "carrier_name": "ACME Trucking",
  "load_id": "L-1001",
  "initial_offer": 2400,
  "agreed_rate": 2250,
  "rounds": 2,
  "outcome": "booked",
  "sentiment": "positive",
  "notes": "Driver available tomorrow",
  "started_at": "2026-05-14T15:01:00",
  "ended_at": "2026-05-14T15:08:00"
}
```

`outcome` ∈ `booked | negotiation_failed | no_match | ineligible | declined | system_error | abandoned`
`sentiment` ∈ `positive | neutral | negative`

### Negotiate payload / response
```json
POST /v1/negotiate
{"call_id": "hr-call-abc123", "load_id": "L-1001", "carrier_offer": 2300}

→ {
  "round": 1,
  "action": "counter",      // "accept" | "counter" | "end"
  "counter_rate": 2257.50,
  "agreed_rate": null,
  "loadboard_rate": 2150.0,
  "ceiling": 2365.0,
  "reason": "Round 1 counter at 105% of loadboard rate."
}
```
The server tracks rounds per `(call_id, load_id)` in the `negotiationround` table. Round caps: 1.05× → 1.08× → 1.10× (ceiling). On round 3, if the carrier offer is above the ceiling the server returns `action=end` and the agent should close with `outcome=negotiation_failed`.

When `outcome=booked` with a `load_id`, the API flips that load's `booked` flag so subsequent searches don't return it.

## Wiring the HappyRobot workflow

Inside the HappyRobot platform, build the workflow as an inbound web call. The nodes that call this API:

1. **Collect MC number** (assistant prompt) → **API call node**: `GET /v1/carriers/verify/{{mc_number}}` with `X-API-Key`. Branch on `eligible == true`. If false, end the call and POST a call event with `outcome=ineligible`.
2. **Collect lane preferences** (origin / destination / equipment) → **API call node**: `GET /v1/loads/search?...`. If empty, POST `outcome=no_match` and end.
3. **Pitch the top result**. Ask if the carrier is interested.
4. **Negotiation loop** (max 3 rounds). For every carrier counter-offer, call **API call node** `POST /v1/negotiate` with `{call_id, load_id, carrier_offer}`. The response tells the agent what to say:
   - `action=accept` → confirm the agreed rate, exit the loop.
   - `action=counter` → quote `counter_rate` to the carrier and loop.
   - `action=end` → close politely, log `outcome=negotiation_failed`.
   Use HappyRobot's built-in `call_id` variable (e.g. `{{call.id}}`) — do **not** rely on `verify_carrier` to return one.
5. **Transfer mock** — prompt the assistant to say "Transferring you to a sales rep now… Transfer was successful, you can wrap up the conversation."
6. **Extract + Classify** nodes — pull `mc_number`, `carrier_name`, `load_id`, `initial_offer`, `agreed_rate`, `rounds`, `outcome`, `sentiment` from the transcript.
7. **API call node** → `POST /v1/calls` with the extracted payload.

## Deploy to Fly.io

Prereqs: install [`flyctl`](https://fly.io/docs/hands-on/install-flyctl/), `fly auth login`.

### Deploy the API
```bash
cd api
# Pick a unique app name, update fly.toml's `app = ...` line first.
fly launch --no-deploy --dockerfile Dockerfile --copy-config
fly volumes create carrier_api_data --region iad --size 1
fly secrets set API_KEY="<long-random-string>" FMCSA_WEB_KEY="<optional>"
# The Dockerfile copies from `api/...` so build from the repo root:
cd .. && fly deploy --config api/fly.toml --dockerfile api/Dockerfile
```

### Deploy the dashboard
```bash
# Update dashboard/fly.toml `app = ...` to be globally unique.
fly secrets set --app <your-dashboard-app> \
  API_BASE_URL="https://<your-api-app>.fly.dev" \
  API_KEY="<same-key-as-above>"
fly deploy --config dashboard/fly.toml --dockerfile dashboard/Dockerfile
```

Both apps get HTTPS + a public hostname automatically. Test the deployment:

```bash
curl https://<your-api-app>.fly.dev/health
curl -H "X-API-Key: $API_KEY" https://<your-api-app>.fly.dev/v1/loads
```

## Environment variables

| Var               | Where      | Notes |
|-------------------|------------|-------|
| `API_KEY`         | api + dashboard | Shared secret. Sent as `X-API-Key`. |
| `FMCSA_WEB_KEY`   | api        | If unset, FMCSA verification runs in deterministic mock mode. |
| `DATABASE_URL`    | api        | Default `sqlite:////data/app.db` (Fly volume mount). |
| `API_BASE_URL`    | dashboard  | URL of the API service. In Docker Compose: `http://api:8000`. |

## Security notes

- All write paths and all data-reading `/v1/*` paths require `X-API-Key`. `/health` is intentionally open for load balancers.
- HTTPS is enforced by Fly's edge (`force_https = true`).
- For real production, rotate the API key, use per-client keys, and put the API behind WAF / rate limiting.

## Project layout

```
api/
  main.py            FastAPI app + routes
  models.py          SQLModel ORM (Load, Call)
  schemas.py         Request/response Pydantic models
  db.py              Engine + seed loader
  config.py          Settings (env-driven)
  auth.py            X-API-Key dependency
  fmcsa.py           QCMobile proxy + mock fallback
  seed_loads.json    8 sample loads
  Dockerfile
  fly.toml
dashboard/
  app.py             Streamlit dashboard
  Dockerfile
  fly.toml
docker-compose.yml   Local dev (API + dashboard + volume)
.env.example
```

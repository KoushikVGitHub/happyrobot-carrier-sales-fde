# Inbound Carrier Sales — Broker Guide

A plain-English overview of what the agent does, how a call flows, and how to read the dashboard. No code knowledge required.

---

## What the agent does

When a carrier calls your inbound number, an AI voice agent picks up. It:

1. **Asks for their MC number** and verifies they're authorized to operate, using the FMCSA federal database.
2. **Asks what lane they're running** (origin, destination, equipment type) and searches your live load board for matches.
3. **Pitches the best matching load** with rate, miles, commodity, and pickup window.
4. **Negotiates the rate** within a 3-round window, capped at 105% / 108% / 110% of loadboard.
5. **Mock-transfers to a sales rep** to finalize the deal (you'd wire this to your real telephony in production).
6. **Logs everything** — transcript, extracted fields, outcome, sentiment — to a dashboard you can review.

A typical call takes 60–90 seconds.

---

## A call walk-through

> **Carrier:** "Hi, my MC number is 44110."
>
> **Agent:** "Got you pulled up — BNR Enterprises Inc. What lane are you running today?"
>
> **Carrier:** "Memphis to St. Louis, reefer."
>
> **Agent:** "I've got a great one — Memphis to St. Louis, 285 miles, 39,000 pounds of frozen foods, picking up tomorrow at 1 PM. Loadboard rate is $850. Want to grab it?"
>
> **Carrier:** "I can do it for $900."
>
> **Agent:** "Best I can do on the first round is $892.50 — that's 105% of loadboard. Work for you?"
>
> **Carrier:** "Yeah, sounds good."
>
> **Agent:** "$892.50 is approved. Transferring you to a sales rep to wrap this up."

That call gets logged with `outcome = booked`, `agreed_rate = 892.50`, `rounds = 2`, `sentiment = positive`.

---

## Reading the dashboard

The dashboard lives at https://happyrobot-carrier-dashboard-kv.fly.dev and refreshes every 15 seconds.

### Top — key numbers

| Metric | What it tells you |
|---|---|
| **Total calls** | How many inbound calls hit the agent today/this week |
| **Booking rate** | % of calls that ended in a booked load |
| **Revenue captured** | Sum of all agreed rates on booked calls |
| **Avg margin** | Average % over loadboard on booked loads |
| **Avg negotiation rounds** | How many rounds it takes on average to close |
| **Positive sentiment %** | Share of calls where the carrier sounded happy |
| **Loads available** | Loads still unbooked vs total in the system |

### Conversion funnel

Shows where calls drop off:
- **Call received** → all calls
- **Eligible (FMCSA)** → carrier passed verification
- **Load matched** → a load was found for their lane
- **Negotiated** → at least one round of rate negotiation
- **Booked** → deal closed

If you see big drops at "Eligible," carriers with bad MCs are calling. If drops are at "Load matched," your inventory doesn't match demand.

### Outcomes chart

Color-coded breakdown of how calls ended. Green is good.

### Loads board

Every load in your system with current status (✅ Available or 🔒 Booked). Loads flip to "Booked" automatically when a call closes with that load.

### Recent calls table

Every call, newest first. Filter by outcome, hover for full data per call.

---

## Outcome glossary

The system classifies each call into one of these 7 outcomes:

| Outcome | Means |
|---|---|
| `booked` | Deal closed at an agreed rate, transferred to sales |
| `negotiation_failed` | 3 rounds without agreement, carrier pushed past ceiling |
| `declined` | Carrier rejected the load before negotiating |
| `no_match` | No load in inventory matched their lane / equipment |
| `ineligible` | FMCSA verification failed — carrier isn't authorized |
| `system_error` | A backend failure prevented completion (rare) |
| `abandoned` | Carrier hung up without a clear outcome |

Sentiment is tracked separately: `positive`, `neutral`, `negative`.

---

## Adding new loads

Loads are stored in a database, seeded from `api/seed_loads.json` on first boot. Two ways to add more:

1. **One-off via API** — POST to `/v1/loads` with the load JSON (see API docs at `/docs`)
2. **Bulk** — edit `seed_loads.json`, wipe the database, redeploy

Each load needs:
- `load_id` (unique, e.g., `L-1021`)
- `origin`, `destination` (city, state)
- `pickup_datetime`, `delivery_datetime` (ISO 8601)
- `equipment_type` (Dry Van / Reefer / Flatbed / Tanker / Power Only / Box Truck)
- `loadboard_rate` (USD)
- `weight` (lbs), `commodity_type`, `num_of_pieces`, `miles`, `dimensions`, `notes`

---

## Rate negotiation ceiling

The agent will never pay more than **110% of loadboard rate** for any load. The 3 rounds are:

| Round | Max counter rate |
|---|---|
| 1 | 105% of loadboard |
| 2 | 108% of loadboard |
| 3 | 110% of loadboard (final) |

If the carrier wants more after round 3, the agent ends the call with `negotiation_failed`. This is the broker's risk control — adjust the multipliers in `api/main.py` if your margin policy changes.

---

## Where the data lives

- **Calls + loads** → SQLite database on a persistent Fly.io volume, encrypted at rest
- **FMCSA lookups** → real-time, no caching; data comes from `mobile.fmcsa.dot.gov`
- **Transcripts** → stored with each call record, viewable in HappyRobot's Runs tab
- **API key** → stored as a Fly secret, never in code

---

## Need help?

- API endpoint reference: https://happyrobot-carrier-api-kv.fly.dev/docs (interactive Swagger UI)
- Live dashboard: https://happyrobot-carrier-dashboard-kv.fly.dev
- Codebase: https://github.com/KoushikVGitHub/happyrobot-carrier-sales-fde

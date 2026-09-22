# Smart Shelf — IoT Spoilage Monitoring Backend

FastAPI async backend for an ESP32-based postharvest spoilage-monitoring shelf designed for MSME kirana stores in India.

The system continuously samples temperature and relative humidity (DHT22) alongside volatile spoilage gases (MQ-135), computes a real-time **Spoilage Risk Index (SRI)** grounded in USDA postharvest respiration kinetics, enforces an **unconditional chilling-injury safety interlock**, and synchronously returns exhaust fan actuation commands to the shelf node.

---

## 1. System Architecture

```text
[ESP32: DHT22 + MQ-135] --HTTP POST /devices/{id}/readings--> [FastAPI Backend] --Async Motor--> [MongoDB Atlas]
         ^                                                            |
         +---------------- Synchronous Fan Command -------------------+
```

1. **ESP32 Shelf Node:** Samples environmental parameters and transmits raw ADC/temperature values via HTTP POST.
2. **FastAPI Ingress Pipeline (Synchronous Critical Path):**
   - Resolves active commodity assignment as of the reading timestamp (`device_assignments`).
   - Resolves effective commodity biological profile (`commodity_profiles`).
   - Resolves active sensor baseline calibration (`device_calibration`).
   - Computes Q10 temperature-excess rate, RH deviation, and normalized gas terms.
   - Computes composite **Spoilage Risk Index (SRI)**.
   - Evaluates the fan command as a strict three-tier cascade — **chilling injury interlock** (unconditional `OFF`) → **gas override** (unconditional `ON`) → **SRI hysteresis** (`sri_fan_on` / `sri_fan_off`). See §3.2; the ordering is load-bearing.
   - Updates alert lifecycle with audit traceability (`opened_by_reading_id`).
   - Persists reading and returns actuation command to the ESP32 in a single sub-second round trip. Persistence and the alert lifecycle are wrapped so that a database failure logs but **never blocks actuation**.
3. **Read-side support:** `GET /devices/{id}/forecast` fits a least-squares trend over recent moving-average buckets and extrapolates SRI; the dashboard (§7) consumes it. Nothing on the read side can affect a fan command.

---

## 2. Postharvest Biological Data & Citations

> [!IMPORTANT]
> **Source of Truth:** Every per-commodity constant in this codebase (temperature bands, RH bands, Q10 coefficients, chilling thresholds, ethylene rates) is loaded strictly from [`commodity-profiles.json`](commodity-profiles.json) and seeded into the MongoDB `commodity_profiles` collection. **No commodity constants are hardcoded in application logic.**

All values are derived directly from **USDA Agriculture Handbook 66 (AH-66)** (*The Commercial Storage of Fruits, Vegetables, and Florist and Nursery Stocks*). Detailed page citations and derivation methodology are documented in [`algorithm-values-reference.md`](algorithm-values-reference.md):

| Commodity | Source Citation | Optimal Temp (°C) | Optimal RH (%) | Chilling Threshold (°C) | $Q_{10}$ (Ambient Band) | Ethylene Prod. ($\mu\text{L/kg/h}$) |
|---|---|---|---|---|---|---|
| **Tomato** | AH-66 pp. 581–585 | 13 – 21 | 90 – 95 | 13.0 | 2.38 ($10\text{--}20^\circ\text{C}$) / 1.95 ($15\text{--}25^\circ\text{C}$) | 1 – 10 (Moderate) |
| **Onion** (cured) | AH-66 pp. 436–439 | 0 – 0 | 65 – 75 | None (Freezes $-0.8^\circ\text{C}$) | 1.14 ($10\text{--}20^\circ\text{C}$) | 0.1 (Very Low) |
| **Potato** (cured) | AH-66 pp. 506–509 | 7 – 10 | 80 – 100 | 2.0 (Mahogany browning) | 1.34 ($10\text{--}20^\circ\text{C}$) | 0.1 (Very Low) |
| **Leafy Greens** (Spinach proxy) | AH-66 pp. 353–355 | 0 – 0 | 95 – 98 | None (Freezes $0^\circ\text{C}$) | 2.09 ($10\text{--}20^\circ\text{C}$) | 0.1 (Very Low) |

---

## 3. Algorithm & Fan Control Specification

### 3.1 SRI Formula (PRD §5.2)

$$\text{tempExcess} = \max(0, T - T_{\text{opt max}})$$
$$\text{tempTerm} = Q_{10}^{\frac{\text{tempExcess}}{10}} - 1$$

$$\text{rhDev} = \max(0, \text{RH}_{\text{opt min}} - \text{RH}, \text{RH} - \text{RH}_{\text{opt max}})$$
$$\text{rhTerm} = \frac{\text{rhDev}}{\text{RH}_{\text{opt max}} - \text{RH}_{\text{opt min}}}$$

$$\text{gasTerm} = \text{normalize}\left(\frac{\text{gasRaw}}{\text{baseline}}\right)$$

$$\text{SRI} = \text{clamp}\left(w_1 \cdot \text{normalize}(\text{tempTerm}) + w_2 \cdot \text{rhTerm} + w_3 \cdot \text{gasTerm}, 0, 1\right)$$

### 3.2 Fan Command Cascade (PRD §5.3)

The fan decision is a strict three-tier cascade, evaluated in this order.
**The ordering is load-bearing and is covered by tests — never reorder it.**

**Tier 1 — Chilling injury interlock (unconditional `off`).** Because an exhaust fan draws in ambient air without active cooling, venting during cold ambient conditions (e.g. night air or air-conditioned rooms) could drop the shelf temperature below the commodity's chilling sensitivity limit, causing irreversible chilling injury (e.g. pitting and uneven ripening in tomatoes below $13^\circ\text{C}$).

$$\text{if } T \le T_{\text{chill}}: \quad \text{fanCommand} = \text{"off"}$$

where $T_{\text{chill}}$ is `chilling_threshold_c`, **falling back to `optimal_temp_min` when that field is null** — true for onion and leafy greens, which have no chilling threshold but do freeze. `optimal_temp_min` therefore looks like decorative metadata and is in fact safety-critical.

**Tier 2 — Gas override (unconditional `on`).** If the normalized gas term *on its own* reaches `gas_override_threshold` (default `0.90`), the fan is forced on and an alert is opened, independent of the composite SRI weighting — a volatile spike must not be diluted by $w_3$.

$$\text{elif } \text{gasTerm} \ge \text{gasOverrideThreshold}: \quad \text{fanCommand} = \text{"on"}$$

**Tier 3 — SRI hysteresis.** Prevents high-frequency relay bouncing.

$$\text{else}: \quad \text{fanCommand} = \text{hysteresis}(\text{SRI}, \text{sriOn}, \text{sriOff}, \text{previousState})$$

The response reports which tier fired via `interlock_triggered` and `gas_override_triggered`.

---

## 4. Database Collections (PRD §3)

1. `commodity_profiles`: Versioned biological reference data seeded from `commodity-profiles.json`. Composite logical key: `(commodity_type, effective_from)`.
2. `devices`: Physical shelf metadata (`device_id`, `location`, `installed_at`).
3. `device_assignments`: Commodity assignment history (`device_id`, `commodity_type`, `start_at`, `end_at`). **Invariant:** At most one assignment per device may have `end_at: null` at any time.
4. `device_calibration`: Versioned sensor baseline calibration records (`device_id`, `mq135_baseline`, `effective_from`).
5. `readings`: Monitored time-series sensor samples with computed SRI and actuation state (`reading_id`, `device_id`, `device_seq`, `device_timestamp`, `temp_c`, `humidity_pct`, `gas_raw`, `sri`, `fan_commanded`).
6. `alerts`: High risk alert episodes with audit link back to the triggering reading (`alert_id`, `device_id`, `opened_by_reading_id`, `peak_sri`, `status`, `opened_at`, `resolved_at`).

---

## 5. API Reference (PRD §4)

| Method | Path | Description |
|---|---|---|
| `POST` | `/devices/{device_id}/readings` | **Critical path:** Push sensor sample, compute SRI, evaluate interlock, return fan command |
| `GET` | `/devices/{device_id}/status` | Latest reading, computed SRI, active fan state, assigned commodity |
| `GET` | `/devices/{device_id}/history` | Time-range and limit query over historical readings |
| `GET` | `/devices/{device_id}/forecast` | Moving-average trend fit and SRI extrapolation (`horizon_minutes` 5–360, default 60; `step_minutes` 1–30, default 5) |
| `GET` | `/devices/{device_id}/alerts` | Alert episode history (filter by `status=open` or `resolved`) |
| `GET` | `/devices/{device_id}/assignment` | Fetch current active commodity assignment (`end_at: null`) |
| `PUT` | `/devices/{device_id}/assignment` | Reassign commodity — closes prior assignment and validates commodity exists |
| `POST` | `/devices` | Register a new shelf device |
| `GET` | `/devices` | List all registered shelf devices |
| `POST` | `/devices/{device_id}/calibration` | Register a new MQ-135 calibration baseline |
| `GET` | `/devices/{device_id}/calibration` | Fetch latest active calibration for device |
| `GET` | `/commodities` | List available commodity profiles and key thresholds |
| `GET` | `/commodities/{commodity_type}` | Get complete profile document for specific commodity |
| `GET` | `/health` | Liveness probe and database connectivity status |

---

## 6. Installation & Quickstart

### 6.1 Prerequisites
- Python 3.11+
- MongoDB instance (MongoDB Atlas connection URI or local MongoDB)

### 6.2 Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 6.3 Configure Environment
Copy [`.env.example`](.env.example) to `.env` and fill in your connection URI. Every setting below is optional except the URI — the defaults in `app/config.py` apply otherwise:
```env
SMART_SHELF_MONGODB_URI=mongodb://localhost:27017
SMART_SHELF_MONGODB_DB_NAME=smart_shelf
SMART_SHELF_W1=0.50
SMART_SHELF_W2=0.30
SMART_SHELF_W3=0.20
SMART_SHELF_SRI_FAN_ON=0.60
SMART_SHELF_SRI_FAN_OFF=0.40
SMART_SHELF_GAS_OVERRIDE_THRESHOLD=0.90
SMART_SHELF_ALERT_THRESHOLD=0.70
SMART_SHELF_ALERT_RESOLVE_THRESHOLD=0.40
```

### 6.4 Seed Database (Idempotent)
Populates `commodity_profiles` verbatim from `commodity-profiles.json`. Running this multiple times is idempotent:
```bash
python scripts/seed_commodity_profiles.py
```

Optionally populate six hours of demo readings so the dashboard has something to draw:
```bash
python scripts/seed_mock_history.py
```

> `scripts/migrate_sri_field_names.py` is a one-off migration (`spoilage_index`→`sri`, `peak_risk_value`→`peak_sri`) for databases seeded before v0.3.0. New installs do not need it.

### 6.5 Run Application Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
- Dashboard: `http://localhost:8000/` (redirects to `/static/dashboard.html`) — see §7
- Interactive Swagger documentation: `http://localhost:8000/docs`

### 6.6 Run Test Suite
The suite uses `mongomock-motor` and needs **no MongoDB** — it will not touch your Atlas cluster:
```bash
pytest -q          # 38 tests
pytest -k interlock -v
```

---

## 7. Dashboard

Served at `/` (redirects to `/static/dashboard.html`). Plain HTML/JS plus Chart.js from a CDN — no build step, no framework.

It is written for the **kirana shopkeeper**, not the evaluator. The hero is a plain-language instruction ("Sell or move these today"), and status is carried by the card's entire background — paper when calm, turmeric when worth watching, chilli red when action is needed — rather than by a badge. Historical SRI, the extrapolated forecast and both threshold guide lines are all still rendered, inside the "Show the readings" disclosure.

- **Bilingual, Hindi-first.** The switch in the masthead persists to `localStorage` (`smartshelf.lang`); default is `hi` (`DEFAULT_LANG` in `static/dashboard.js`). Every user-facing string lives in the `COPY` object at the top of that file, so a third language is a new key rather than a code change.
- **Typography is Anek Latin + Anek Devanagari**, two cuts of one Ek Type superfamily sharing `wght 100..800` and `wdth 75..125`. That shared width axis is the only reason the condensed headline survives the language switch; substituting any other Devanagari face breaks it.
- **Polls every 45 seconds:** `/devices`, `/devices/{id}/status`, `/devices/{id}/history`, `/devices/{id}/forecast`.
- **Two shopkeeper-facing numbers are derived in the browser**, not returned by the API — time until spoilage risk as $(\text{alertThreshold} - \text{SRI}) / \text{trendSlopePerMin}$, and "fan on since" from the unbroken `fan_commanded` run at the head of history.
- **"I've moved them" is a local acknowledgement only** — there is no write endpoint for it. It is stored in `localStorage`, expires after 8 hours, and clears itself if SRI climbs more than 0.03 above the acknowledged value, so it can never hide a worsening shelf.

> [!NOTE]
> The Hindi copy has **not been reviewed by a native speaker**. It drives a money decision ("sell these today"), so have someone check it before a real shop relies on it.

Run `python scripts/seed_mock_history.py` to give it data to draw.

---

## 8. Algorithm Tuning Constants & Open Questions (PRD §7)

The following parameters are engineering tuning weights and operational thresholds separate from biological data. Default placeholders are defined in `app/config.py` and can be customized via environment variables:

1. **SRI Term Weights (`w1, w2, w3`):** Default `w1=0.50` (temperature), `w2=0.30` (RH deviation), `w3=0.20` (gas signal). *(# TODO(confirm): Confirm empirical weighting).*
2. **Gas Normalization Baseline & Span (`gas_signal_baseline`, `gas_signal_span`):** Normalizes $\frac{R_s}{R_o}$ relative to calibrated clean air baseline. *(# TODO(confirm): Finalize MQ-135 voltage span under kirana ambient conditions).*
3. **Gas Override Threshold (`gas_override_threshold=0.90`):** Normalized gas term at which the fan is forced on and an alert opened, independent of the composite SRI weighting. *(# TODO(confirm): Validate against MQ-135 response to ethylene and ammonia at kirana concentrations).*
4. **Fan Hysteresis (`sri_fan_on=0.60`, `sri_fan_off=0.40`):** Prevents high-frequency relay bouncing.
5. **Alert Thresholds (`alert_threshold=0.70`, `alert_resolve_threshold=0.40`):** Sets the trigger and resolution bounds for spoilage risk notifications.

---

## 9. Firmware Contract

A generic reference sketch lives at [`firmware/smart_shelf/smart_shelf.ino`](firmware/smart_shelf/smart_shelf.ino).

- **Endpoint:** `POST {BACKEND_BASE_URL}/devices/{device_id}/readings`, `Content-Type: application/json`.
- **Body:** `{"device_seq": int, "temp_c": float, "humidity_pct": float, "gas_raw": float, "sensor_status": "ok"}`. `temp_c`/`humidity_pct`/`gas_raw` must be finite (no `NaN`/`Infinity`) or the backend returns `422`; if the DHT22 read fails, skip the send instead of transmitting it.
- **`device_timestamp` is optional and should be omitted by firmware with no synced clock** (no RTC, no NTP) — the backend stamps it with server time when absent. If a device *does* buffer readings during a WiFi outage and replays them, it may send a genuine past `device_timestamp` for each buffered sample; the backend trusts any timestamp from 2024-01-01 onward that isn't more than 5 minutes ahead of server time, and only overrides implausible values (unset clocks, garbled clocks).
- **`fan_command`** in the response is always exactly `"on"` or `"off"` — drive the relay directly from that string, never compute it on-device.
- **Setup order before a device can POST readings:** register the device (`POST /devices`) → assign it a commodity (`PUT /devices/{device_id}/assignment`) → register a calibration baseline (`POST /devices/{device_id}/calibration`). Missing an assignment or profile is a `404`; missing calibration is a `400`.
- **MQ-135 wiring:** use an ADC1 pin (GPIO32–39) — ADC2 pins are unusable once WiFi is active on the ESP32. Most breakout boards output 0–5V-ish and need a resistive divider to stay under the ESP32 ADC's ~3.3V max input, or reads will clip/saturate.

---

## 10. Repository Layout

```text
app/
  main.py              app factory, lifespan, CORS, static mount, /  ->  dashboard
  config.py            engineering tunables ONLY (never commodity constants)
  db.py                Motor client, dependency, startup index creation
  routers/             health · readings · devices · commodities · forecasting
  services/            spoilage (critical path) · device_service · forecasting
  models/              Pydantic wire + document schemas, UtcDatetime helpers
scripts/               seed_commodity_profiles · seed_mock_history · migrate_sri_field_names
static/                dashboard.html · dashboard.js
firmware/smart_shelf/  smart_shelf.ino (reference sketch)
tests/                 38 tests, no MongoDB required
docs/CODEMAPS/         token-lean architecture maps (architecture, backend, frontend, data, dependencies)
commodity-profiles.json        USDA AH-66 source data — the citation layer
algorithm-values-reference.md  citation derivations
PRD.md                         specification of record, cited by section throughout the code
```

### Conventions worth knowing before editing

- **`PRD.md` is the spec of record.** Code cites it by section (`PRD §5.2`). Where code and PRD disagree, ask rather than silently picking one.
- **No commodity constant is ever hardcoded.** They load from `commodity-profiles.json`; `app/config.py` holds engineering tunables only.
- **Datetimes must use `UtcDatetime` / `OptionalUtcDatetime`** from `app/models/common.py`. The Motor client is not built `tz_aware`, so a plain `datetime` annotation serializes with no offset and clients read it as local time.
- **`Field(description=...)` strings are published** in `/openapi.json` — editing one changes an API response, not just a comment.
- **`device_calibration` is singular** while every other collection is plural. Existing schema; not a typo.
- **`fan_command` and `fan_commanded` are not duplicates.** The string is the wire protocol in `ReadingResponse`; the bool is persisted on the reading and read back to drive hysteresis.
- **Seeding is idempotent** via a SHA256 content hash per commodity block. Profiles are versioned by `(commodity_type, effective_from)` and never updated in place.

There is no linter, formatter, type-checker, or build step configured.

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
   - Checks **Chilling Injury Safety Interlock** (forces fan `OFF` if ambient temperature $\le$ chilling threshold).
   - Evaluates hysteresis fan state (`sri_on` / `sri_off`).
   - Updates alert lifecycle with audit traceability (`opened_by_reading_id`).
   - Persists reading and returns actuation command to the ESP32 in a single sub-second round trip.

---

## 2. Postharvest Biological Data & Citations

> [!IMPORTANT]
> **Source of Truth:** Every per-commodity constant in this codebase (temperature bands, RH bands, Q10 coefficients, chilling thresholds, ethylene rates) is loaded strictly from [`commodity-profiles.json`](file:///Users/prajwal/Documents/IoT-Based%20project/commodity-profiles.json) and seeded into the MongoDB `commodity_profiles` collection. **No commodity constants are hardcoded in application logic.**

All values are derived directly from **USDA Agriculture Handbook 66 (AH-66)** (*The Commercial Storage of Fruits, Vegetables, and Florist and Nursery Stocks*). Detailed page citations and derivation methodology are documented in [`algorithm-values-reference.md`](file:///Users/prajwal/Documents/IoT-Based%20project/algorithm-values-reference.md):

| Commodity | Source Citation | Optimal Temp (°C) | Optimal RH (%) | Chilling Threshold (°C) | $Q_{10}$ (Ambient Band) | Ethylene Prod. ($\mu\text{L/kg/h}$) |
|---|---|---|---|---|---|---|
| **Tomato** | AH-66 pp. 581–585 | 13 – 21 | 90 – 95 | 13.0 | 2.38 ($10\text{--}20^\circ\text{C}$) / 1.95 ($15\text{--}25^\circ\text{C}$) | 1 – 10 (Moderate) |
| **Onion** (cured) | AH-66 pp. 436–439 | 0 – 0 | 65 – 75 | None (Freezes $-0.8^\circ\text{C}$) | 1.14 ($10\text{--}20^\circ\text{C}$) | 0.1 (Very Low) |
| **Potato** (cured) | AH-66 pp. 506–509 | 7 – 10 | 80 – 100 | 2.0 (Mahogany browning) | 1.34 ($10\text{--}20^\circ\text{C}$) | 0.1 (Very Low) |
| **Leafy Greens** (Spinach proxy) | AH-66 pp. 353–355 | 0 – 0 | 95 – 98 | None (Freezes $0^\circ\text{C}$) | 2.09 ($10\text{--}20^\circ\text{C}$) | 0.1 (Very Low) |

---

## 3. Algorithm & Safety Interlock Specification

### 3.1 SRI Formula (PRD §5.2)

$$\text{tempExcess} = \max(0, T - T_{\text{opt max}})$$
$$\text{tempTerm} = Q_{10}^{\frac{\text{tempExcess}}{10}} - 1$$

$$\text{rhDev} = \max(0, \text{RH}_{\text{opt min}} - \text{RH}, \text{RH} - \text{RH}_{\text{opt max}})$$
$$\text{rhTerm} = \frac{\text{rhDev}}{\text{RH}_{\text{opt max}} - \text{RH}_{\text{opt min}}}$$

$$\text{gasTerm} = \text{normalize}\left(\frac{\text{gasRaw}}{\text{baseline}}\right)$$

$$\text{SRI} = \text{clamp}\left(w_1 \cdot \text{normalize}(\text{tempTerm}) + w_2 \cdot \text{rhTerm} + w_3 \cdot \text{gasTerm}, 0, 1\right)$$

### 3.2 Chilling Injury Safety Interlock (PRD §5.3)

Because an exhaust fan draws in ambient air without active cooling, venting during cold ambient conditions (e.g. night air or air-conditioned rooms) could drop the shelf temperature below the commodity's chilling sensitivity limit, causing irreversible chilling injury (e.g. pitting and uneven ripening in tomatoes below $13^\circ\text{C}$).

$$\text{if } T \le T_{\text{chilling threshold}}: \quad \text{fanCommand} = \text{"off"} \quad (\text{unconditional interlock})$$
$$\text{else}: \quad \text{fanCommand} = \text{hysteresis}(\text{SRI}, \text{sriOn}, \text{sriOff}, \text{previousState})$$

---

## 4. Database Collections (PRD §3)

1. `commodity_profiles`: Versioned biological reference data seeded from `commodity-profiles.json`. Composite logical key: `(commodity_type, effective_from)`.
2. `devices`: Physical shelf metadata (`device_id`, `location`, `installed_at`).
3. `device_assignments`: Commodity assignment history (`device_id`, `commodity_type`, `start_at`, `end_at`). **Invariant:** At most one assignment per device may have `end_at: null` at any time.
4. `device_calibration`: Versioned sensor baseline calibration records (`device_id`, `mq135_baseline`, `effective_from`).
5. `readings`: Monitored time-series sensor samples with computed SRI and actuation state (`reading_id`, `device_id`, `device_seq`, `device_timestamp`, `temp_c`, `humidity_pct`, `gas_raw`, `spoilage_index`, `fan_commanded`).
6. `alerts`: High risk alert episodes with audit link back to the triggering reading (`alert_id`, `device_id`, `opened_by_reading_id`, `peak_risk_value`, `status`, `opened_at`, `resolved_at`).

---

## 5. API Reference (PRD §4)

| Method | Path | Description |
|---|---|---|
| `POST` | `/devices/{device_id}/readings` | **Critical path:** Push sensor sample, compute SRI, evaluate interlock, return fan command |
| `GET` | `/devices/{device_id}/status` | Latest reading, computed SRI, active fan state, assigned commodity |
| `GET` | `/devices/{device_id}/history` | Time-range and limit query over historical readings |
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
Create a `.env` file in the project root:
```env
SMART_SHELF_MONGODB_URI=mongodb://localhost:27017
SMART_SHELF_MONGODB_DB_NAME=smart_shelf
SMART_SHELF_W1=0.50
SMART_SHELF_W2=0.30
SMART_SHELF_W3=0.20
SMART_SHELF_SRI_ON=0.60
SMART_SHELF_SRI_OFF=0.40
SMART_SHELF_ALERT_THRESHOLD=0.70
SMART_SHELF_ALERT_RESOLVE_THRESHOLD=0.40
```

### 6.4 Seed Database (Idempotent)
Populates `commodity_profiles` verbatim from `commodity-profiles.json`. Running this multiple times is idempotent:
```bash
python scripts/seed_commodity_profiles.py
```

### 6.5 Run Application Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive Swagger documentation will be available at: `http://localhost:8000/docs`.

### 6.6 Run Test Suite
```bash
pytest -v
```

---

## 7. Algorithm Tuning Constants & Open Questions (PRD §7)

The following parameters are engineering tuning weights and operational thresholds separate from biological data. Default placeholders are defined in `app/config.py` and can be customized via environment variables:

1. **SRI Term Weights (`w1, w2, w3`):** Default `w1=0.50` (temperature), `w2=0.30` (RH deviation), `w3=0.20` (gas signal). *(# TODO(confirm): Confirm empirical weighting).*
2. **Gas Normalization Baseline & Span (`gas_signal_baseline`, `gas_signal_span`):** Normalizes $\frac{R_s}{R_o}$ relative to calibrated clean air baseline. *(# TODO(confirm): Finalize MQ-135 voltage span under kirana ambient conditions).*
3. **Fan Hysteresis (`sri_on=0.60`, `sri_off=0.40`):** Prevents high-frequency relay bouncing.
4. **Alert Thresholds (`alert_threshold=0.70`, `alert_resolve_threshold=0.40`):** Sets the trigger and resolution bounds for spoilage risk notifications.

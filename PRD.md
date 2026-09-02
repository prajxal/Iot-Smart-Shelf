# Smart Shelf — Software PRD
**Component scope:** FastAPI backend, MongoDB (Compass/Atlas) integration, fan control algorithm
**Project:** IoT capstone — ESP32 spoilage-monitoring shelf for MSME kirana stores
**Status:** Draft v0.3 — schema adopted from user's ERD, patched; algorithm source-locked to reference files

---

## 0. Required source files (read this before writing any code)

Two files live in the project root and are the **only** source of truth for algorithm constants:

- **`commodity-profiles.json`** — per-commodity Q10, temperature/RH bands, ethylene data, extracted from USDA Agriculture Handbook 66.
- **`algorithm-values-reference.md`** — the same values with page citations and derivation notes, for humans and for code comments.

**Rule for whoever/whatever implements this (including AI coding agents): every numeric constant used in the spoilage algorithm — temperature bands, RH bands, Q10, ethylene thresholds — must be read from `commodity-profiles.json` (directly, or via the seeded `commodity_profiles` collection described below) at runtime or at seed-time. None of these values may be hardcoded, approximated, or invented in application code.** If a commodity or field is needed but missing from the JSON, that's a stop-and-ask situation, not a fill-in-a-plausible-number situation — flag it back to the project owner rather than guessing. This project already had one citation-fabrication incident during the literature survey; the same discipline applies here.

---

## 1. Purpose

Define the software layer that sits between the ESP32 hardware node (DHT22 + MQ-135 + relay-controlled exhaust fan) and the data store, and that computes the Spoilage Risk Index (SRI) used to decide whether the fan runs. This PRD covers only the software: API contract, database schema, and the decision algorithm — not firmware or enclosure design.

---

## 2. System Overview

```
[ESP32: DHT22 + MQ-135] --HTTP POST--> [FastAPI] --write--> [MongoDB Atlas]
                                            |                     ^
                                            | read (profiles,     |
                                            |  history, status)   |
                                            v                     |
                                     [MongoDB Compass] -----------+
                                     (GUI for dev/inspection, not
                                      an app-facing component)
```

- **ESP32** sends raw readings to FastAPI and receives a fan command synchronously.
- **FastAPI** resolves the active commodity + calibration for the device, computes SRI, persists the reading, and returns the fan command in one round trip.
- **MongoDB Compass** is a GUI client only — inspects the same Atlas cluster the API writes to.

---

## 3. Data Model (MongoDB)

This adopts the ERD you provided, with two additions (§3.7) needed to make the algorithm computable without inventing values elsewhere. Every collection maps 1:1 to an ERD entity; FK notation from the diagram becomes a string reference field in Mongo (no enforced joins — the API layer is responsible for referential integrity, noted per-collection below).

### 3.1 `commodity_profiles`
Versioned reference data — the diagram's `(commodity_type, effective_from)` composite key. Seeded from `commodity-profiles.json`, never hand-edited with guessed numbers.
```json
{
  "_id": "tomato__2026-09-02",
  "commodity_type": "tomato",
  "effective_from": "ISODate",
  "optimal_temp_min": 13,
  "optimal_temp_max": 21,
  "optimal_rh_min": 90,
  "optimal_rh_max": 95,
  "chilling_sensitivity": "chilling injury below 13C (mature-green)",
  "reference_temp_c": 20,
  "q10": 2.2,
  "chilling_threshold_c": 13,
  "ethylene_production_uL_kg_h": [1, 10],
  "ethylene_sensitivity_uL_L": 0.5,
  "source": "AH-66 pp. 581-585"
}
```
Current-version lookup: query `commodity_type == X`, `effective_from <= as_of`, sorted descending by `effective_from`, take the first result. Index on `{commodity_type: 1, effective_from: -1}`.

### 3.2 `devices`
```json
{
  "_id": "shelf-01",
  "device_id": "shelf-01",
  "location": "kirana-store-A",
  "installed_at": "ISODate"
}
```
No `commodity` field here by design — that's what `device_assignments` is for. This is the part of your schema I'd flag as the single biggest improvement over my first draft: it means reassigning a shelf to a different commodity later doesn't lose the history of what it was monitoring before.

### 3.3 `device_assignments`
```json
{
  "_id": "ObjectId",
  "assignment_id": "asg-001",
  "device_id": "shelf-01",
  "commodity_type": "tomato",
  "start_at": "ISODate",
  "end_at": null
}
```
Current-assignment lookup: `device_id == X`, `end_at == null` (or `end_at > as_of` for a historical lookup). **API-enforced invariant, not DB-enforced:** at most one assignment per device may have `end_at: null` at a time. `PUT /devices/{id}/assignment` must close out (`end_at = now`) any existing open assignment before opening a new one, in the same request.

### 3.4 `device_calibration`
```json
{
  "_id": "ObjectId",
  "calibration_id": "cal-001",
  "device_id": "shelf-01",
  "mq135_baseline": 76.4,
  "effective_from": "ISODate"
}
```
Current-calibration lookup: same pattern as commodity_profiles — latest `effective_from <= as_of` for that `device_id`.

### 3.5 `readings`
```json
{
  "_id": "ObjectId",
  "reading_id": "rd-000001",
  "device_id": "shelf-01",
  "device_seq": 4821,
  "device_timestamp": "ISODate",
  "server_received_at": "ISODate",
  "temp_c": 24.6,
  "humidity_pct": 68.2,
  "gas_raw": 412,
  "sensor_status": "ok",
  "spoilage_index": 0.62,
  "fan_commanded": true
}
```
- `device_seq` + `device_timestamp` vs `server_received_at`: keep both. If the ESP32 ever buffers readings during a WiFi drop and replays them, `device_seq` lets you detect gaps/reordering and `server_received_at` tells you when the backend actually saw it — don't collapse these into one timestamp.
- `sensor_status`: surface DHT22/MQ-135 read failures here (`"ok"`, `"dht22_error"`, `"mq135_error"`) rather than silently writing a null or zero reading that would corrupt the SRI calculation.
- Raw `gas_raw` is stored, not a precomputed Rs/Ro ratio — the ratio is derived at computation time using whichever `device_calibration.mq135_baseline` was active at `device_timestamp`. This is more correct (recalibration doesn't retroactively corrupt historical readings) but means every SRI computation — live or replayed — must resolve calibration the same way. Don't compute Rs/Ro once and cache it as if it were raw data.

### 3.6 `alerts`
```json
{
  "_id": "ObjectId",
  "alert_id": "alt-001",
  "device_id": "shelf-01",
  "alert_type": "high_spoilage_risk",
  "status": "open",
  "opened_at": "ISODate",
  "resolved_at": null,
  "peak_risk_value": 0.81,
  "opened_by_reading_id": "rd-000001"
}
```
One addition vs. the diagram: **`opened_by_reading_id`**. The diagram's `may_open` relationship from `readings` isn't backed by a stored field in `alerts` — without it, an alert can't be traced back to the specific reading that triggered it, which matters for your defense ("show me the exact sensor values that caused this alert"). Keep it optional/nullable so it doesn't break the relationship if an alert is ever opened by other means.

### 3.7 Patches to the diagram (edge cases found)

1. **`commodity_profiles` was missing the fields the algorithm actually needs.** The diagram has `optimal_temp_min/max`, `optimal_rh_min/max`, and `chilling_sensitivity` (string) — no Q10, no reference temperature, no ethylene data. Without these, the algorithm has nowhere to pull its temperature-response curve from except hardcoded guesses, which directly violates §0. Added: `reference_temp_c`, `q10`, `chilling_threshold_c` (numeric, alongside the existing human-readable `chilling_sensitivity` string), `ethylene_production_uL_kg_h`, `ethylene_sensitivity_uL_L` — all copied verbatim from `commodity-profiles.json`.
2. **No overlap protection on `device_assignments`.** Two open assignments for the same device would make "what commodity is this shelf monitoring right now" ambiguous. Enforced at the API layer (see §3.3), not by MongoDB.
3. **Alerts couldn't be traced back to a triggering reading.** Added `opened_by_reading_id` (see §3.6).
4. **Chilling threshold matters here even though the shelf can't actively cool.** An exhaust fan pulls in ambient air — if ambient is colder than `optimal_temp_min` (realistic on a cool night or an air-conditioned store), running the fan to chase a high SRI could push the shelf *below* the commodity's chilling threshold and cause the exact injury you're trying to prevent. This is why `chilling_threshold_c` is now a first-class numeric field instead of just a descriptive string — the algorithm needs to compare against it directly (see §5.3, safety interlock).

---

## 4. FastAPI Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/devices/{device_id}/readings` | ESP32 pushes a sensor sample; returns computed `fan_command` synchronously |
| `GET` | `/devices/{device_id}/status` | Latest reading + SRI + current fan state + active commodity |
| `GET` | `/devices/{device_id}/history` | Time-range query over `readings` |
| `GET` | `/devices/{device_id}/alerts` | Alert history (open + resolved) |
| `GET` | `/devices/{device_id}/assignment` | Current commodity assignment |
| `PUT` | `/devices/{device_id}/assignment` | Reassign commodity — closes prior open assignment, opens a new one |
| `GET` | `/commodities` | List available commodity profiles (id, current version, key thresholds) |
| `GET` | `/health` | Liveness check |

Notes:
- `POST /readings` is the critical path: validate → resolve assignment → resolve profile → resolve calibration → compute SRI → check safety interlock → persist → respond with command, in one round trip.
- `PUT /devices/{id}/assignment` should reject a `commodity_type` that has no matching document in `commodity_profiles`.

---

## 5. Fan Control Algorithm

Grounded in USDA Handbook 66 spoilage-rate data via `commodity-profiles.json` / `algorithm-values-reference.md` — no values in this section are hardcoded per-commodity numbers; they're all field names to be resolved from the database at evaluation time.

### 5.1 Resolve context for the reading
1. `commodity_type` ← active `device_assignments` row for this `device_id` as of `device_timestamp`.
2. `profile` ← latest `commodity_profiles` row for that `commodity_type` with `effective_from <= device_timestamp`.
3. `calibration` ← latest `device_calibration` row for this `device_id` with `effective_from <= device_timestamp`.
4. `gas_signal` ← derive Rs/Ro (or calibrated ppm-equivalent) from `gas_raw` and `calibration.mq135_baseline`.

### 5.2 Compute SRI
```
temp_excess = max(0, temp_c - profile.optimal_temp_max)
temp_term   = profile.q10 ** (temp_excess / 10) - 1        # 0 when within/below optimal max

rh_dev      = max(0, profile.optimal_rh_min - humidity_pct, humidity_pct - profile.optimal_rh_max)
rh_band     = profile.optimal_rh_max - profile.optimal_rh_min
rh_term     = rh_dev / rh_band                              # 0 when within band

gas_term    = normalize(gas_signal)                          # commodity-agnostic baseline for now — see Open Questions

SRI = clamp(w1 * normalize(temp_term) + w2 * rh_term + w3 * gas_term, 0, 1)
```
`w1, w2, w3` and the `normalize()` scaling functions are tunable constants, not commodity data — they can live in application config, separate from the commodity-derived values.

### 5.3 Safety interlock (new — see §3.7.4)
```
if temp_c <= profile.chilling_threshold_c (or optimal_temp_min if no explicit threshold):
    fan_command = "off"      # never vent in air that risks chilling injury, regardless of SRI
else:
    fan_command = hysteresis(SRI, sri_on, sri_off, previous_state)
```

### 5.4 Alerting
If `SRI >= alert_threshold` and no `alerts` document is currently `open` for this device, open one with `opened_by_reading_id` set to this reading and `peak_risk_value = SRI`. While open, update `peak_risk_value` on any later reading with a higher SRI. Close (`status: "resolved"`, `resolved_at`) once SRI drops below a resolve threshold for some minimum duration (avoid flapping).

### 5.5 Output per reading
- `fan_command`: `on` / `off`
- `spoilage_index`: the SRI value, persisted on the reading itself (per your schema — no separate `sri_scores` collection)

---

## 6. Non-Functional Notes
- **Latency:** the four-lookup resolution chain in §5.1 should be fast — index every collection on its lookup key (`{commodity_type,effective_from}`, `{device_id,effective_from}`, `{device_id,end_at}`) so this stays sub-second.
- **Resilience:** if the MongoDB write fails, still return a fan command computed from the current sample — don't block actuation on persistence.
- **Auditability:** `opened_by_reading_id` + versioned `commodity_profiles`/`device_calibration` mean any historical SRI value can be recomputed and explained after the fact — useful for the panel defense.

---

## 7. Open Questions — please fill in

1. **Sampling and evaluation frequency.** How often does the ESP32 sample and post? Is SRI recomputed every sample?
2. **Gas term baseline.** `gas_term` above is commodity-agnostic — the MQ-135 reads one physical signal regardless of which commodity is assigned. If gas-threshold interpretation should actually vary per commodity, that needs its own field on `commodity_profiles` (not currently in the JSON either — would need a follow-up extraction).
3. **`w1, w2, w3` weights and `sri_on`/`sri_off`/`alert_threshold` values.** These are tuning constants, not sourced from Handbook 66 — they need to come from your own calibration/testing, not the reference files.
4. **Auth/security scope.** Defense-demo-only, or device tokens?
5. **Connectivity fallback.** Any local ESP32 fallback logic if the backend is unreachable?
6. **Data retention.** Any expectation to downsample/archive `readings`?

**Resolved:** commodity scope (multi-commodity via `device_assignments`, versioned), schema shape (adopted from your ERD with the four patches in §3.7), and algorithm structure (Q10 + RH-band + gas-term composite with a chilling-injury safety interlock).

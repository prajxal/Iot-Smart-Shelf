# Smart Shelf — Algorithm Values Reference
Derived from USDA Agriculture Handbook 66 (AH-66), *The Commercial Storage of Fruits, Vegetables, and Florist and Nursery Stocks*. Page numbers below refer to the AH-66 PDF you supplied.

Companion file: `commodity-profiles.json` — the same values in machine-readable form for your coding agent to load directly as config.

---

## 1. Why four commodities

Kirana stores stock mixed perishables. Rather than guess, I pulled full data for the four most representative candidates: **tomato, onion, potato, leafy greens (spinach used as the documented proxy — AH-66 states other cooking greens are "expected to be in the same general range" but doesn't have separate tables for them, p.354)**. If your actual shelf targets a narrower or different set, tell me and I'll pull the exact chapters.

## 2. Per-commodity values

### Tomato (AH-66 pp. 581–585)
| | |
|---|---|
| Optimal storage temp | 13–21 °C (mature-green); 7–15 °C (red-ripe, brief) |
| Ripening conditions | 19–21 °C, 90–95% RH |
| Retail display temp | ~20 °C |
| Chilling injury threshold | <13 °C for mature-green fruit |
| Respiration (mg CO₂/kg/h) | 10 °C: 14.5 · 15 °C: 22 · 20 °C: 34.5 · 25 °C: 43 |
| Q10 | 2.38 (10–20 °C) · 1.95 (15–25 °C) |
| Ethylene production | 1–10 µL/kg/h at 20 °C — moderate |
| Ethylene sensitivity | 0.5 µL/L triggers ripening — very sensitive |

### Onion, dry/cured (AH-66 pp. 436–439)
| | |
|---|---|
| Optimal storage temp | 0 °C |
| Optimal RH | 65–75% |
| Chilling sensitivity | None — freezes at −0.8 °C; sprouts faster above 4 °C |
| Respiration (mg CO₂/kg/h) | 0 °C: 3 · 5 °C: 5 · 10 °C: 7 · 15 °C: 7 · 20 °C: 8 |
| Q10 | 2.33 (0–10 °C) · **1.14** (10–20 °C) |
| Ethylene production | <0.1 µL/kg/h — very low |
| Ethylene sensitivity | Only >1500 µL/L triggers sprouting — very low |

### Potato (AH-66 pp. 506–509)
| | |
|---|---|
| Optimal storage temp | 7–10 °C (fresh use) · 10–15 °C (frying) · 15–20 °C (chipping) |
| Curing conditions | 20 °C, 80–100% RH |
| Chilling injury threshold | 1–2 °C (internal browning); 3–4 °C causes irreversible sugar buildup |
| Respiration, mature/cured (mg CO₂/kg/h) | 5 °C: 12 · 10 °C: 16 · 15 °C: 16.5 · 20 °C: 21.5 |
| Q10 | **1.38** (5–15 °C) · **1.34** (10–20 °C) |
| Ethylene production | <0.1 µL/kg/h — very low |
| Special constraint | Needs darkness (greening/solanine) — a shelf physical-design constraint, not a fan-control one |

### Leafy Greens (spinach data, AH-66 pp. 353–355)
| | |
|---|---|
| Optimal storage temp | ~0 °C |
| Optimal RH | 95–98% |
| Chilling sensitivity | None — store as cold as possible without freezing |
| Shelf life | ~2 weeks properly cold-handled; much shorter at kirana ambient with no active cooling |
| Respiration (mg CO₂/kg/h) | 0 °C: 20.5 · 5 °C: 45 · 10 °C: 110 · 15 °C: 178.5 · 20 °C: 229.5 |
| Q10 | 5.37 (0–10 °C) · 3.97 (5–15 °C) · **2.09** (10–20 °C) |
| Ethylene production | <0.1 µL/kg/h — very low, but highly **sensitive** to external ethylene (yellowing/senescence) |

## 3. What this data tells you before you write any code

- **Respiration at 20 °C, low → high: onion (8) < potato (21.5) < tomato (34.5) ≪ leafy greens (229.5).** Leafy greens spoil roughly 7–10× faster than tomato at the same temperature. If your defense needs to *show* the system detecting meaningful spoilage within a short demo window, leafy greens (or tomato) make a far more convincing single-commodity choice than onion or potato, which are naturally shelf-stable and would show little change even over several days.
- **Tomato and leafy greens have an ethylene interaction**: tomato is a moderate ethylene producer, leafy greens are ethylene-sensitive. If your shelf design puts multiple commodities in one monitored zone, this is a real, citable postharvest-science reason not to co-locate them — worth a sentence in your report regardless of what the algorithm does with it.
- **Q10 is not constant** for any of these commodities — it changes across temperature bands (see onion: 2.33 at 0–10 °C vs. 1.14 at 10–20 °C). A single fixed Q10 constant in your algorithm is a simplification; state it as one in your paper rather than presenting it as if it were a physical constant. This is exactly the kind of detail a panel is likely to probe.
- **Important limitation to state explicitly**: AH-66's data tops out at 20–25 °C because it's written for commercial cold-chain storage. Kirana store ambient temperatures in India routinely exceed that (30–40 °C in summer). Applying these Q10 values above 25 °C is an extrapolation beyond the measured range — flagging this yourself, rather than having a panelist catch it, is the safer move given the citation-fabrication issue earlier in this project.
- **This shelf has no active cooling — only an exhaust fan.** AH-66's "optimal storage temperature" figures assume refrigerated cold storage, which a fan alone cannot achieve. The honest framing for your algorithm is: minimize deviation from ambient-achievable conditions and vent accumulated heat/humidity/spoilage-gas buildup, not "hit 0 °C for onions." Say this directly in the PRD/paper rather than implying the fan replicates cold storage.

## 4. Open decisions before the formula is final

1. **Which commodity(ies) is the shelf actually built for?** Everything above is prepared for all four so you can decide; the algorithm itself should probably be tuned to just one or two for a capstone-scale build.
2. **Reference temperature for the Q10 term.** I'd suggest using the *retail display* temperature where AH-66 gives one (tomato: 20 °C) rather than the wholesale cold-storage optimum, since that's the achievable baseline for an ambient kirana shelf. For onion/potato/greens, AH-66 doesn't give a retail-display figure — you'd need to either pick a reasoned proxy (e.g., ambient reference of 25–30 °C) or state the assumption explicitly.
3. **RH sensor is not in your BOM** (only DHT22 for temp/humidity is listed — good, DHT22 does give both — but confirm the RH weighting in SRI is intended). This part is already covered by the DHT22, just flagging that RH deviation should use the bands above.
4. **Gas sensor (MQ-135) calibration** against these ethylene/respiration numbers — the sensor detects a broad mix of gases (CO2, NH3, and some VOCs), not ethylene specifically, so the "ethylene production" figures above are context, not a direct calibration target. If you want, I can help define what the MQ-135 signal is actually functioning as a proxy for in your specific setup once you share your calibration approach.

---

Once you tell me which commodity(ies) you're actually targeting, I can fold this into the PRD's algorithm section with the real thresholds and hysteresis band, replacing the placeholders from the first draft.

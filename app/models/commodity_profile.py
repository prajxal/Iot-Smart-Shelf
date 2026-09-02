"""Pydantic models for commodity_profiles collection.

Source of truth for fields: PRD §3.1, §3.7, and commodity-profiles.json.
All numeric values are loaded directly from USDA Handbook 66 reference data.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field
from app.models.common import PyObjectId


class CommodityProfile(BaseModel):
    """Versioned commodity reference data profile.

    Represents a document in the `commodity_profiles` collection.
    Composite logical key: (commodity_type, effective_from).
    """

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    commodity_type: str = Field(..., description="Unique identifier of commodity (e.g., tomato, onion)")
    effective_from: datetime = Field(..., description="Timestamp from which this profile version is effective")

    # Temperature bands (°C)
    optimal_temp_min: Optional[float] = Field(default=None, description="Minimum optimal storage temperature in Celsius")
    optimal_temp_max: Optional[float] = Field(default=None, description="Maximum optimal storage temperature in Celsius")

    # Relative Humidity bands (%)
    optimal_rh_min: Optional[float] = Field(default=None, description="Minimum optimal relative humidity percentage")
    optimal_rh_max: Optional[float] = Field(default=None, description="Maximum optimal relative humidity percentage")

    # Chilling injury fields (PRD §3.7.1, §3.7.4)
    chilling_sensitivity: Optional[str] = Field(default=None, description="Qualitative description of chilling vulnerability")
    chilling_threshold_c: Optional[float] = Field(
        default=None,
        description="Numeric chilling injury temperature threshold in Celsius (fan safety interlock limit)",
    )

    # Respiration and Q10 kinetics (PRD §3.7.1)
    reference_temp_c: Optional[float] = Field(
        default=None,
        description="Baseline reference temperature in Celsius (e.g., retail display or curing baseline)",
    )
    q10: Optional[float] = Field(
        default=None,
        description="Q10 temperature coefficient representing factor increase in respiration per 10°C rise",
    )
    q10_bands: Optional[Dict[str, float]] = Field(
        default=None,
        description="Temperature-specific Q10 values from AH-66 (e.g., {'10_20': 2.38, '15_25': 1.95})",
    )

    # Ethylene production and sensitivity (PRD §3.7.1)
    ethylene_production_uL_kg_h: Optional[Union[float, List[float]]] = Field(
        default=None,
        description="Ethylene production rate in µL/kg/h at 20°C (scalar or range [min, max])",
    )
    ethylene_sensitivity_uL_L: Optional[float] = Field(
        default=None,
        description="External ethylene sensitivity threshold in µL/L",
    )

    # Citation metadata and hash for idempotency
    source: Optional[str] = Field(default=None, description="Citation page in USDA Handbook 66")
    content_hash: Optional[str] = Field(default=None, description="SHA256 hash of source JSON commodity block")
    raw_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Complete raw JSON block from commodity-profiles.json for lossless persistence",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    def get_q10(self, temp_c: Optional[float] = None) -> float:
        """Resolve the most accurate Q10 value for the given temperature.

        If temperature-specific bands are available in `q10_bands`, select the
        closest band to `temp_c`.
        - Inside band (e.g., 10-20°C or 15-25°C): returns that band's Q10.
        - Above highest band (e.g. ambient > 25°C): returns highest band (extrapolation per AH-66 note).
        - Below lowest band: returns lowest band.
        Otherwise falls back to self.q10 or 1.0.
        """
        if self.q10_bands and temp_c is not None:
            parsed_bands: List[Tuple[float, float, float]] = []
            for band_key, band_val in self.q10_bands.items():
                try:
                    low_s, high_s = band_key.split("_")
                    low, high = float(low_s), float(high_s)
                    if low <= temp_c <= high:
                        return band_val
                    parsed_bands.append((low, high, band_val))
                except (ValueError, IndexError):
                    continue

            if parsed_bands:
                parsed_bands.sort(key=lambda x: x[1])
                if temp_c > parsed_bands[-1][1]:
                    return parsed_bands[-1][2]
                if temp_c < parsed_bands[0][0]:
                    return parsed_bands[0][2]

        if self.q10 is not None:
            return self.q10
        return 1.0


class CommodityProfileSummary(BaseModel):
    """Summarized commodity profile view for listing."""

    commodity_type: str
    effective_from: datetime
    optimal_temp_min: Optional[float] = None
    optimal_temp_max: Optional[float] = None
    optimal_rh_min: Optional[float] = None
    optimal_rh_max: Optional[float] = None
    chilling_threshold_c: Optional[float] = None
    reference_temp_c: Optional[float] = None
    q10: Optional[float] = None
    source: Optional[str] = None

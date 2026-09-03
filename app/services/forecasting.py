"""Forecasting service for Spoilage Risk Index (SRI) extrapolation.

Aggregates historical readings into time-based moving average buckets,
fits a least-squares linear trend over recent buckets, and extrapolates
future SRI trajectories.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import logging
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.models.forecast import DeviceForecastResponse, ForecastPoint

logger = logging.getLogger("smart_shelf.forecasting")


def ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime object is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_time_moving_averages(
    readings: List[Dict[str, Any]],
    ref_time: datetime,
    window_minutes: float = 30.0,
    bucket_step_minutes: float = 5.0,
    bucket_half_width_minutes: float = 7.5,
) -> List[Tuple[datetime, float]]:
    """Compute moving average of the `spoilage_index` field in time-based buckets.

    Buckets are centered every `bucket_step_minutes` (e.g. -25m, -20m, ..., 0m from ref_time).
    Each bucket averages all readings with device_timestamp within ±`bucket_half_width_minutes`
    of that center timestamp.

    Returns:
        List of (bucket_center_time, ma_value) tuples for all non-empty buckets in chronological order.
    """
    ref_utc = ensure_utc(ref_time)
    num_steps = int(window_minutes // bucket_step_minutes)
    valid_buckets: List[Tuple[datetime, float]] = []

    for i in range(num_steps):
        # i=0 -> offset = -(25m), i=5 -> offset = 0m
        offset_minutes = -(window_minutes - bucket_step_minutes) + (i * bucket_step_minutes)
        center_time = ref_utc + timedelta(minutes=offset_minutes)

        # Find readings within ±bucket_half_width_minutes
        sri_values: List[float] = []
        for r in readings:
            ts = r.get("device_timestamp")
            sri = r.get("spoilage_index")
            if ts is not None and sri is not None:
                ts_utc = ensure_utc(ts)
                diff_sec = abs((ts_utc - center_time).total_seconds())
                if diff_sec <= bucket_half_width_minutes * 60.0:
                    sri_values.append(float(sri))

        if sri_values:
            ma_val = sum(sri_values) / len(sri_values)
            valid_buckets.append((center_time, ma_val))

    return valid_buckets


def fit_linear_trend_slope(points: List[Tuple[datetime, float]]) -> float:
    """Fit a least-squares linear trend over data points (t_i, y_i) to get slope per minute.

    Requires at least 2 points (typically called with 4).
    """
    if len(points) < 2:
        return 0.0

    t0 = points[0][0]
    x_vals = [(p[0] - t0).total_seconds() / 60.0 for p in points]
    y_vals = [p[1] for p in points]
    n = len(points)

    x_bar = sum(x_vals) / n
    y_bar = sum(y_vals) / n

    numerator = sum((x - x_bar) * (y - y_bar) for x, y in zip(x_vals, y_vals))
    denominator = sum((x - x_bar) ** 2 for x in x_vals)

    if denominator == 0.0:
        return 0.0

    return numerator / denominator


class ForecastingService:
    """Service to compute and extrapolate SRI forecasts for smart shelf devices."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db

    async def generate_forecast(
        self,
        device_id: str,
        horizon_minutes: int = 60,
        step_minutes: int = 5,
        as_of: Optional[datetime] = None,
    ) -> DeviceForecastResponse:
        """Generate SRI forecast for a device.

        Steps:
        1. Fetch readings for device_id from the last 40 minutes (covers >= 37.5m back).
        2. Resolve active commodity and thresholds (fan_threshold, alert_threshold).
        3. Check distinct raw readings count and compute time-based moving average buckets.
        4. If <4 distinct readings OR <4 valid MA buckets, return insufficient_data=True.
        5. Fit least-squares linear trend over the last 4 MA buckets to get trend_slope_per_min.
        6. Extrapolate predicted_sri(t) = last_MA_value + trend_slope_per_min * minutes_ahead.
        7. Clamp predicted_sri floor to 0.0 (no upper ceiling).
        """
        now = ensure_utc(as_of or datetime.now(timezone.utc))
        fetch_cutoff = now - timedelta(minutes=40)

        # 1. Fetch recent readings
        cursor = self.db["readings"].find(
            {
                "device_id": device_id,
                "device_timestamp": {"$gte": fetch_cutoff, "$lte": now},
            }
        ).sort("device_timestamp", 1)
        recent_readings = await cursor.to_list(length=1000)

        # 2. Resolve active commodity & thresholds
        assignment = await self.db["device_assignments"].find_one(
            {
                "device_id": device_id,
                "start_at": {"$lte": now},
                "$or": [{"end_at": None}, {"end_at": {"$gt": now}}],
            },
            sort=[("start_at", -1)],
        )
        commodity_type: Optional[str] = assignment.get("commodity_type") if assignment else None

        profile_doc = None
        if commodity_type:
            profile_doc = await self.db["commodity_profiles"].find_one(
                {
                    "commodity_type": commodity_type,
                    "effective_from": {"$lte": now},
                },
                sort=[("effective_from", -1)],
            )

        fan_threshold = (profile_doc.get("sri_on") if profile_doc else None) or settings.sri_on
        alert_threshold = (profile_doc.get("alert_threshold") if profile_doc else None) or settings.alert_threshold

        # Latest raw SRI
        current_sri: Optional[float] = None
        if recent_readings:
            for r in reversed(recent_readings):
                if r.get("spoilage_index") is not None:
                    current_sri = float(r["spoilage_index"])
                    break

        if current_sri is None:
            latest_doc = await self.db["readings"].find_one(
                {"device_id": device_id, "device_timestamp": {"$lte": now}},
                sort=[("device_timestamp", -1)],
            )
            if latest_doc and latest_doc.get("spoilage_index") is not None:
                current_sri = float(latest_doc["spoilage_index"])

        # 3. Check distinct raw readings count
        distinct_raw_readings = {
            r.get("reading_id") or str(r.get("device_timestamp"))
            for r in recent_readings
            if r.get("spoilage_index") is not None
        }

        # 4. Compute moving average buckets
        ma_buckets = compute_time_moving_averages(
            readings=recent_readings,
            ref_time=now,
            window_minutes=30.0,
            bucket_step_minutes=5.0,
            bucket_half_width_minutes=7.5,
        )

        # 5. Dual guard: must have >=4 distinct readings AND >=4 MA buckets
        if len(distinct_raw_readings) < 4 or len(ma_buckets) < 4:
            return DeviceForecastResponse(
                device_id=device_id,
                generated_at=now,
                commodity=commodity_type,
                current_sri=current_sri,
                fan_threshold=fan_threshold,
                alert_threshold=alert_threshold,
                trend_slope_per_min=None,
                insufficient_data=True,
                forecast=None,
            )

        # 6. Fit least-squares linear trend over the last 4 MA buckets
        last_4_buckets = ma_buckets[-4:]
        trend_slope_per_min = fit_linear_trend_slope(last_4_buckets)
        last_ma_value = last_4_buckets[-1][1]

        # 7. Extrapolate future steps
        forecast_points: List[ForecastPoint] = []
        for m in range(step_minutes, horizon_minutes + 1, step_minutes):
            pred_sri = max(0.0, last_ma_value + (trend_slope_per_min * m))
            point_time = now + timedelta(minutes=m)
            forecast_points.append(
                ForecastPoint(
                    timestamp=point_time,
                    predicted_sri=round(pred_sri, 4),
                )
            )

        return DeviceForecastResponse(
            device_id=device_id,
            generated_at=now,
            commodity=commodity_type,
            current_sri=current_sri,
            fan_threshold=fan_threshold,
            alert_threshold=alert_threshold,
            trend_slope_per_min=round(trend_slope_per_min, 6),
            insufficient_data=False,
            forecast=forecast_points,
        )

"""Mock data seeder for Smart Shelf Dashboard testing.

Seeds 6 hours of realistic sensor trajectories and rising SRI for shelf-01
so the dashboard can be visually inspected and tested immediately.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.db import db_manager, init_db_indexes
from app.config import settings


async def seed_mock_dashboard_data():
    print("Connecting to MongoDB...")
    db = db_manager.connect()
    await init_db_indexes(db)

    now = datetime.now(timezone.utc)
    device_id = "shelf-01"

    # 1. Ensure device exists
    await db["devices"].update_one(
        {"device_id": device_id},
        {
            "$set": {
                "device_id": device_id,
                "location": "Kirana Store A — Main Display",
                "installed_at": now - timedelta(days=7),
            }
        },
        upsert=True,
    )

    # 2. Ensure active assignment to tomato
    await db["device_assignments"].update_one(
        {"device_id": device_id, "end_at": None},
        {
            "$set": {
                "assignment_id": "asg-demo-01",
                "device_id": device_id,
                "commodity_type": "tomato",
                "start_at": now - timedelta(days=7),
                "end_at": None,
            }
        },
        upsert=True,
    )

    # 3. Ensure calibration
    await db["device_calibration"].update_one(
        {"device_id": device_id},
        {
            "$set": {
                "calibration_id": "cal-demo-01",
                "device_id": device_id,
                "mq135_baseline": 100.0,
                "effective_from": now - timedelta(days=7),
            }
        },
        upsert=True,
    )

    # 4. Clean previous mock readings for this device
    await db["readings"].delete_many({"device_id": device_id})

    # 5. Generate 6 hours of readings spaced every 2 minutes (180 points)
    # Realistic trajectory: SRI rises gradually from 0.22 to 0.58
    readings = []
    total_minutes = 6 * 60
    step = 2

    for m in range(0, total_minutes + 1, step):
        ts = now - timedelta(minutes=total_minutes - m)
        progress = m / total_minutes
        
        # Rising SRI trajectory
        sri = 0.22 + (0.36 * progress)
        temp_c = 22.0 + (3.5 * progress)
        humidity_pct = 92.0 - (4.0 * progress)
        gas_raw = 100.0 + (160.0 * progress)

        readings.append(
            {
                "reading_id": f"rd-mock-{m:04d}",
                "device_id": device_id,
                "device_seq": m // step + 1,
                "device_timestamp": ts,
                "server_received_at": ts + timedelta(seconds=1),
                "temp_c": round(temp_c, 1),
                "humidity_pct": round(humidity_pct, 1),
                "gas_raw": round(gas_raw, 1),
                "sensor_status": "ok",
                "spoilage_index": round(sri, 4),
                "fan_commanded": sri >= settings.sri_on,
            }
        )

    await db["readings"].insert_many(readings)
    print(f"Successfully seeded {len(readings)} readings for '{device_id}' across the past 6 hours.")
    print("Open http://localhost:8000/static/dashboard.html to view the live dashboard.")


if __name__ == "__main__":
    asyncio.run(seed_mock_dashboard_data())

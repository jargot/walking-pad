#!/usr/bin/env python3
"""
Stateless WalkingPad Stop Script
Always: discover -> connect -> stop -> save to DB -> disconnect
"""

import asyncio
import time
import yaml
import os
import psycopg2
import requests
from datetime import datetime, timedelta
from ph4_walkingpad.pad import WalkingPad, Controller
from ph4_walkingpad.utils import setup_logging
from bleak import BleakScanner
from dotenv import load_dotenv

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}")

def reset_bleak_cache():
    """Force clear Bleak's internal BLE adapter state (helps intermittent failures)."""
    try:
        log_with_timestamp("🔄 Resetting Bleak BLE cache after connection failure...")

        # Clear Bleak's global scanner instances
        import bleak
        if hasattr(bleak, '_scanner_backends'):
            bleak._scanner_backends.clear()

        # Force garbage collection to clear any lingering BLE state
        import gc
        gc.collect()

        log_with_timestamp("✅ Bleak cache reset complete")
        return True
    except Exception as e:
        log_with_timestamp(f"⚠️  Bleak reset failed: {e}")
        return False

def load_config():
    """Load WalkingPad address from config"""
    load_dotenv()

    walkingpad_address = os.getenv('WALKINGPAD_ADDRESS')
    if walkingpad_address:
        return walkingpad_address

    # Fallback to config.yaml
    with open("config.yaml", 'r') as stream:
        config = yaml.safe_load(stream)
        return config['address']

def store_in_db(steps, distance_in_km, duration_in_seconds):
    """Store workout data in database"""
    db_host = os.getenv('DB_HOST')
    if not db_host:
        log_with_timestamp("No database configured, skipping save")
        return False

    conn = None
    cur = None
    try:
        log_with_timestamp(f"💾 Saving to database: {steps} steps, {distance_in_km:.2f}km, {duration_in_seconds}s")

        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 5432),
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD')
        )
        cur = conn.cursor()

        date_today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        duration = int(duration_in_seconds / 60)

        cur.execute(
            "INSERT INTO exercise VALUES (%s, %s, %s, %s)",
            (date_today, steps, duration, distance_in_km)
        )
        conn.commit()
        log_with_timestamp("✅ Workout saved to database")
        return True

    except Exception as e:
        log_with_timestamp(f"❌ Database error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def log_to_fitbit(steps, duration_minutes, start_time_str):
    """Log walking activity to Fitbit"""
    access_token = os.getenv('FITBIT_ACCESS_TOKEN')

    if not access_token:
        log_with_timestamp("⚠️  No Fitbit token found, skipping Fitbit logging")
        return False

    try:
        data = {
            'activityId': 90013,  # Walking activity ID
            'startTime': start_time_str,
            'durationMillis': duration_minutes * 60 * 1000,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'distance': steps,
            'distanceUnit': 'steps'
        }

        log_with_timestamp(f"🏃 Sending to Fitbit: {steps} steps, {start_time_str}->{duration_minutes}min")
        log_with_timestamp(f"📤 Payload: {data}")

        url = "https://api.fitbit.com/1/user/-/activities.json"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        response = requests.post(url, headers=headers, data=data)

        if response.status_code == 401:
            log_with_timestamp("⚠️  Fitbit token expired, run setup_fitbit_oauth.py")
            return False

        response.raise_for_status()
        result = response.json()

        activity_log = result.get('activityLog', {})
        log_with_timestamp(f"📥 Response: steps={activity_log.get('steps', 0)}, calories={activity_log.get('calories', 0)}, logId={activity_log.get('logId', 'Unknown')}")
        log_with_timestamp(f"✅ Logged to Fitbit successfully")
        return True

    except Exception as e:
        log_with_timestamp(f"❌ Fitbit logging failed: {e}")
        return False

async def discover_walkingpad(address, timeout=15):
    """Discover the WalkingPad device"""
    log_with_timestamp(f"🔍 Discovering WalkingPad {address}...")

    devices = await BleakScanner.discover(timeout=timeout)

    for device in devices:
        if device.address.upper() == address.upper():
            log_with_timestamp(f"✅ Found WalkingPad: {device.name} ({device.address})")
            return device

    raise Exception(f"WalkingPad {address} not found in {len(devices)} discovered devices")

async def stop_walking(address):
    """Complete stop walking sequence with database save, with retries/timeouts."""
    start_time = time.time()
    workout_data = {"steps": 0, "distance": 0.0, "time": 0}
    workout_start_time = None  # Track actual workout start time

    # Step 1: Connect with a retry after resetting Bleak cache (mirrors start_walk.py)
    controller = Controller()
    controller.log_messages_info = False

    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                log_with_timestamp(f"🔄 Attempt {attempt + 1} after Bleak reset...")
            log_with_timestamp(f"📱 Connecting directly to WalkingPad {address}...")
            connect_start = time.time()
            await asyncio.wait_for(controller.run(address), timeout=8.0)
            connect_elapsed = time.time() - connect_start
            log_with_timestamp(f"⏱️  Connection completed in {connect_elapsed:.1f}s")
            break
        except Exception as e:
            if attempt == 0:
                log_with_timestamp(f"⚠️  First connection attempt failed: {e}")
                reset_bleak_cache()
                await asyncio.sleep(1)
                continue
            else:
                elapsed = time.time() - start_time
                log_with_timestamp(f"❌ Stop walk failed after BLE reset attempt: {e}")
                return {"success": False, "error": str(e), "time": elapsed, "workout": workout_data}

    try:
        # Step 2: Get current stats before stopping (with retries)
        log_with_timestamp("📊 Getting workout statistics...")
        stats = None

        # Try multiple times to get valid workout stats
        for attempt in range(3):
            try:
                await asyncio.wait_for(controller.ask_stats(), timeout=3.0)
                await asyncio.sleep(0.5)  # Give more time for response

                if hasattr(controller, 'last_status') and controller.last_status:
                    stats = controller.last_status
                    # Only accept if we have actual workout data (steps > 0 or time > 0)
                    if stats.steps > 0 or stats.time > 0:
                        workout_data["steps"] = stats.steps
                        workout_data["distance"] = stats.dist / 100  # Convert to km
                        workout_data["time"] = stats.time
                        log_with_timestamp(f"📈 Workout stats: {workout_data['steps']} steps, {workout_data['distance']:.2f}km, {workout_data['time']}s")
                        break
                    else:
                        log_with_timestamp(f"⚠️  Attempt {attempt + 1}: Got zero stats (steps={stats.steps}, time={stats.time}), retrying...")
                else:
                    log_with_timestamp(f"⚠️  Attempt {attempt + 1}: No status available, retrying...")

            except Exception as e:
                log_with_timestamp(f"⚠️  Attempt {attempt + 1} failed: {e}")

            if attempt < 2:  # Don't sleep after last attempt
                await asyncio.sleep(1)

        # Final check - if we still don't have valid stats, warn but continue
        if workout_data["steps"] == 0 and workout_data["time"] == 0:
            log_with_timestamp("❌ Could not retrieve valid workout statistics after 3 attempts")
        else:
            log_with_timestamp(f"✅ Successfully retrieved workout stats")

        # Step 3: Stop sequence
        log_with_timestamp("🛑 Stopping walk sequence...")

        log_with_timestamp("  → Switching to STANDBY mode")
        await asyncio.wait_for(controller.switch_mode(WalkingPad.MODE_STANDBY), timeout=3.0)
        await asyncio.sleep(0.1)

        log_with_timestamp("  → Getting final history")
        await asyncio.wait_for(controller.ask_hist(1), timeout=3.0)
        await asyncio.sleep(0.1)

        # Step 4: Save to database
        db_success = store_in_db(
            workout_data["steps"],
            workout_data["distance"],
            workout_data["time"]
        )

        # Step 4b: Log to Fitbit
        fitbit_success = False
        if workout_data["steps"] > 0 and workout_data["time"] > 0:
            # Calculate workout start time (workout end time minus workout duration)
            workout_end = datetime.now()
            workout_duration_seconds = workout_data["time"]
            workout_start_time = workout_end - timedelta(seconds=workout_duration_seconds)
            start_time_str = workout_start_time.strftime("%H:%M")
            duration_minutes = max(1, round(workout_duration_seconds / 60))  # At least 1 minute

            fitbit_success = log_to_fitbit(
                workout_data["steps"],
                duration_minutes,
                start_time_str
            )

        # Step 5: Disconnect cleanly (best effort)
        log_with_timestamp("📱 Disconnecting...")
        try:
            await asyncio.wait_for(controller.disconnect(), timeout=3.0)
        except Exception as e:
            log_with_timestamp(f"⚠️  Disconnect warning: {e}")

        elapsed = time.time() - start_time
        log_with_timestamp(f"✅ Walk stopped successfully in {elapsed:.1f}s")

        return {
            "success": True,
            "message": "Walk stopped",
            "time": elapsed,
            "workout": workout_data,
            "saved_to_db": db_success,
            "logged_to_fitbit": fitbit_success
        }

    except Exception as e:
        elapsed = time.time() - start_time
        log_with_timestamp(f"❌ Stop walk failed after {elapsed:.1f}s: {e}")
        return {"success": False, "error": str(e), "time": elapsed, "workout": workout_data}

async def main():
    """Main entry point"""
    try:
        address = load_config()
        result = await stop_walking(address)
        return result
    except Exception as e:
        log_with_timestamp(f"Fatal error: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    result = asyncio.run(main())
    if not result["success"]:
        exit(1)

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

# Performance Profiles - Choose your poison!
SAFE_CONFIG = {
    "connection_timeout": 8.0,
    "stats_retries": 3,
    "stats_timeout": 3.0,
    "stats_sleep": 0.5,
    "retry_sleep": 1.0,
    "command_timeout": 3.0,
    "disconnect_timeout": 3.0,
    "name": "SAFE"
}

LUDICROUS_CONFIG = {
    "connection_timeout": 5.0,
    "stats_retries": 2,
    "stats_timeout": 2.0,
    "stats_sleep": 0.2,
    "retry_sleep": 0.5,
    "command_timeout": 2.0,
    "disconnect_timeout": 2.0,
    "name": "LUDICROUS"
}

# Choose your config here - set to LUDICROUS_CONFIG for speed, SAFE_CONFIG for reliability
PERFORMANCE_CONFIG = SAFE_CONFIG

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

async def ensure_advertising(address, timeout=3.0):
    """Quickly confirm the WalkingPad is advertising"""
    try:
        from bleak import BleakScanner
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        return device is not None
    except Exception as exc:
        log_with_timestamp(f"⚠️  Advertising check failed: {exc}")
        return False

async def stop_walking(address):
    """Complete stop walking sequence with database save, with retries/timeouts."""
    start_time = time.time()
    workout_data = {"steps": 0, "distance": 0.0, "time": 0}
    workout_start_time = None  # Track actual workout start time

    log_with_timestamp(f"🚀 Using {PERFORMANCE_CONFIG['name']} performance profile")

    # Step 0: Check if device is advertising (quick pre-flight check)
    advertising_present = await ensure_advertising(address, timeout=2.0)
    log_with_timestamp(f"📡 Device advertising: {advertising_present}")

    # Step 1: Enhanced connection with better retry logic
    controller = Controller()
    controller.log_messages_info = False

    max_attempts = 3  # Increase attempts since pad is running
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                log_with_timestamp(f"🔄 Attempt {attempt + 1}/{max_attempts} after reset...")

            # Try discovery first for running pads (they might need re-discovery)
            if attempt > 0 or not advertising_present:
                try:
                    log_with_timestamp(f"🔍 Quick discovery for running pad...")
                    await discover_walkingpad(address, timeout=5)
                    log_with_timestamp(f"✅ Discovery successful")
                except Exception as discovery_error:
                    log_with_timestamp(f"⚠️  Discovery failed: {discovery_error}")

            log_with_timestamp(f"📱 Connecting to WalkingPad {address}...")
            connect_start = time.time()
            await asyncio.wait_for(controller.run(address), timeout=PERFORMANCE_CONFIG["connection_timeout"])
            connect_elapsed = time.time() - connect_start
            log_with_timestamp(f"⏱️  Connection completed in {connect_elapsed:.1f}s")
            break
        except Exception as e:
            error_msg = str(e)
            log_with_timestamp(f"⚠️  Attempt {attempt + 1} failed: {error_msg}")

            if attempt < max_attempts - 1:  # Not the last attempt
                # More aggressive reset for running pads
                reset_bleak_cache()
                await asyncio.sleep(PERFORMANCE_CONFIG["retry_sleep"] * (attempt + 1))  # Progressive delay
                continue
            else:
                elapsed = time.time() - start_time
                log_with_timestamp(f"❌ All {max_attempts} connection attempts failed")
                return {"success": False, "error": f"Connection failed after {max_attempts} attempts: {error_msg}", "time": elapsed, "workout": workout_data}

    try:
        # Step 2: Get current stats before stopping (with retries)
        log_with_timestamp("📊 Getting workout statistics...")
        stats = None

        # Try multiple times to get valid workout stats
        for attempt in range(PERFORMANCE_CONFIG["stats_retries"]):
            try:
                await asyncio.wait_for(controller.ask_stats(), timeout=PERFORMANCE_CONFIG["stats_timeout"])
                await asyncio.sleep(PERFORMANCE_CONFIG["stats_sleep"])  # Give time for response

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

            if attempt < PERFORMANCE_CONFIG["stats_retries"] - 1:  # Don't sleep after last attempt
                await asyncio.sleep(PERFORMANCE_CONFIG["retry_sleep"])

        # Final check - if we still don't have valid stats, warn but continue
        if workout_data["steps"] == 0 and workout_data["time"] == 0:
            log_with_timestamp(f"❌ Could not retrieve valid workout statistics after {PERFORMANCE_CONFIG['stats_retries']} attempts")
        else:
            log_with_timestamp(f"✅ Successfully retrieved workout stats")

        # Step 3: Stop sequence
        log_with_timestamp("🛑 Stopping walk sequence...")

        log_with_timestamp("  → Switching to STANDBY mode")
        await asyncio.wait_for(controller.switch_mode(WalkingPad.MODE_STANDBY), timeout=PERFORMANCE_CONFIG["command_timeout"])
        await asyncio.sleep(0.1)

        log_with_timestamp("  → Getting final history")
        await asyncio.wait_for(controller.ask_hist(1), timeout=PERFORMANCE_CONFIG["command_timeout"])
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
            await asyncio.wait_for(controller.disconnect(), timeout=PERFORMANCE_CONFIG["disconnect_timeout"])
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

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
from datetime import datetime
from ph4_walkingpad.pad import WalkingPad, Controller
from ph4_walkingpad.utils import setup_logging
from bleak import BleakScanner
from dotenv import load_dotenv

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}")

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
    """Complete stop walking sequence with database save"""
    start_time = time.time()
    workout_data = {"steps": 0, "distance": 0.0, "time": 0}

    try:
        # Step 1: Discover
        device = await discover_walkingpad(address)

        # Step 2: Connect
        log_with_timestamp("📱 Connecting to WalkingPad...")
        controller = Controller()
        controller.log_messages_info = False
        await controller.run(address)

        # Step 3: Get current stats before stopping
        log_with_timestamp("📊 Getting workout statistics...")
        await controller.ask_stats()
        await asyncio.sleep(0.5)

        if hasattr(controller, 'last_status') and controller.last_status:
            stats = controller.last_status
            workout_data["steps"] = stats.steps
            workout_data["distance"] = stats.dist / 100  # Convert to km
            workout_data["time"] = stats.time

            log_with_timestamp(f"📈 Workout stats: {workout_data['steps']} steps, {workout_data['distance']:.2f}km, {workout_data['time']}s")

        # Step 4: Stop sequence
        log_with_timestamp("🛑 Stopping walk sequence...")

        log_with_timestamp("  → Switching to STANDBY mode")
        await controller.switch_mode(WalkingPad.MODE_STANDBY)
        await asyncio.sleep(0.7)

        log_with_timestamp("  → Getting final history")
        await controller.ask_hist(1)
        await asyncio.sleep(0.7)

        # Step 5: Save to database
        db_success = store_in_db(
            workout_data["steps"],
            workout_data["distance"],
            workout_data["time"]
        )

        # Step 6: Disconnect cleanly
        log_with_timestamp("📱 Disconnecting...")
        await controller.disconnect()

        elapsed = time.time() - start_time
        log_with_timestamp(f"✅ Walk stopped successfully in {elapsed:.1f}s")

        return {
            "success": True,
            "message": "Walk stopped",
            "time": elapsed,
            "workout": workout_data,
            "saved_to_db": db_success
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
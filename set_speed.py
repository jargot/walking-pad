#!/usr/bin/env python3
"""
Stateless WalkingPad Speed Control Script
Always: connect -> set speed -> disconnect
"""

import asyncio
import time
import yaml
import os
import json
import signal
import sys
from datetime import datetime
from ph4_walkingpad.pad import WalkingPad, Controller
from bleak import BleakScanner
from dotenv import load_dotenv


def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}")


def log_metric(event_type, **data):
    """Emit structured metrics for downstream analysis"""
    entry = {
        "event": event_type,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    entry.update(data)
    print(f"[METRIC] {json.dumps(entry, sort_keys=True)}")


def load_config():
    """Load WalkingPad address from config"""
    load_dotenv()

    walkingpad_address = os.getenv('WALKINGPAD_ADDRESS')
    if walkingpad_address:
        return walkingpad_address

    with open("config.yaml", 'r') as stream:
        config = yaml.safe_load(stream)
        return config['address']


async def ensure_advertising(address, timeout=3.0):
    """Quickly confirm the WalkingPad is advertising"""
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        return device is not None
    except Exception as exc:
        log_with_timestamp(f"⚠️  Advertising check failed: {exc}")
        return False


async def set_speed(address, speed):
    """Connect to WalkingPad and set speed."""
    start_time = time.time()
    controller = None

    try:
        # Preflight
        step_start = time.time()
        advertising_present = await ensure_advertising(address, timeout=2.0)
        log_with_timestamp(f"📡 Device advertising: {advertising_present} ({time.time() - step_start:.1f}s)")
        log_metric("preflight", advertising=advertising_present, elapsed=round(time.time() - step_start, 2))

        if not advertising_present:
            return {"success": False, "error": "WalkingPad not found", "time": time.time() - start_time}

        # Connect
        connect_start = time.time()
        log_with_timestamp(f"📱 Connecting to WalkingPad {address}...")
        controller = Controller()
        controller.log_messages_info = False
        await asyncio.wait_for(controller.run(address), timeout=6.0)
        connect_elapsed = time.time() - connect_start
        log_with_timestamp(f"⏱️  Connection completed in {connect_elapsed:.1f}s")
        log_metric("connection", elapsed=round(connect_elapsed, 2), status="connected")

        # Set speed
        step_start = time.time()
        speed_kmh = speed / 10.0
        log_with_timestamp(f"🏃 Setting speed to {speed_kmh:.1f} km/h (raw: {speed})")
        await controller.change_speed(speed)
        await asyncio.sleep(0.05)
        log_with_timestamp(f"⏱️  Speed set in {time.time() - step_start:.1f}s")

        # Disconnect
        step_start = time.time()
        log_with_timestamp("📱 Disconnecting...")
        await controller.disconnect()
        controller = None
        log_with_timestamp(f"⏱️  Disconnect in {time.time() - step_start:.1f}s")

        elapsed = time.time() - start_time
        log_with_timestamp(f"✅ Speed set to {speed_kmh:.1f} km/h in {elapsed:.1f}s")
        log_metric("set_speed", success=True, speed=speed, total_time=round(elapsed, 2))

        return {"success": True, "message": f"Speed set to {speed_kmh} km/h", "time": elapsed}

    except Exception as e:
        elapsed = time.time() - start_time
        log_with_timestamp(f"❌ Set speed failed after {elapsed:.1f}s: {e}")
        log_metric("set_speed", success=False, speed=speed, total_time=round(elapsed, 2), error=type(e).__name__)
        return {"success": False, "error": str(e), "time": elapsed}
    finally:
        if controller:
            try:
                await asyncio.wait_for(controller.disconnect(), timeout=3.0)
            except Exception:
                pass


async def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: set_speed.py <speed>  (speed in 0.1 km/h units, e.g. 30 = 3.0 km/h)")
        sys.exit(1)

    speed = int(sys.argv[1])
    if speed < 0 or speed > 60:
        print(f"Speed {speed} out of range (0-60, i.e. 0-6.0 km/h)")
        sys.exit(1)

    try:
        address = load_config()
        result = await set_speed(address, speed)
        return result
    except Exception as e:
        log_with_timestamp(f"Fatal error: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    result = asyncio.run(main())
    if not result["success"]:
        exit(1)

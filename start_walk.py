#!/usr/bin/env python3
"""
Stateless WalkingPad Start Script
Always: discover -> connect -> start -> disconnect
"""

import asyncio
import time
import yaml
import os
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

async def discover_walkingpad(address, timeout=15):
    """Discover the WalkingPad device"""
    discover_start = time.time()
    log_with_timestamp(f"🔍 Discovering WalkingPad {address}...")

    devices = await BleakScanner.discover(timeout=timeout)
    discover_elapsed = time.time() - discover_start
    log_with_timestamp(f"⏱️  Discovery completed in {discover_elapsed:.1f}s - found {len(devices)} devices")

    for device in devices:
        if device.address.upper() == address.upper():
            log_with_timestamp(f"✅ Found WalkingPad: {device.name} ({device.address})")
            return device

    raise Exception(f"WalkingPad {address} not found in {len(devices)} discovered devices")

async def start_walking(address):
    """Complete start walking sequence"""
    start_time = time.time()

    try:
        # Step 1: Connect directly to known address
        connect_start = time.time()
        log_with_timestamp(f"📱 Connecting directly to WalkingPad {address}...")
        controller = Controller()
        controller.log_messages_info = False
        await controller.run(address)
        connect_elapsed = time.time() - connect_start
        log_with_timestamp(f"⏱️  Connection completed in {connect_elapsed:.1f}s")

        # Step 3: Start sequence (optimized)
        sequence_start = time.time()
        log_with_timestamp("🚀 Starting walk sequence...")

        step_start = time.time()
        log_with_timestamp("  → Switching to MANUAL mode")
        await controller.switch_mode(WalkingPad.MODE_MANUAL)
        await asyncio.sleep(0.1)  # Minimal delay for BLE command processing
        step_elapsed = time.time() - step_start
        log_with_timestamp(f"    ⏱️  MANUAL switch: {step_elapsed:.1f}s")

        step_start = time.time()
        log_with_timestamp("  → Starting belt")
        await controller.start_belt()
        await asyncio.sleep(0.1)  # Minimal delay for BLE command processing
        step_elapsed = time.time() - step_start
        log_with_timestamp(f"    ⏱️  Belt start: {step_elapsed:.1f}s")

        sequence_elapsed = time.time() - sequence_start
        log_with_timestamp(f"⏱️  Walk sequence completed in {sequence_elapsed:.1f}s")

        # Step 4: Disconnect cleanly
        disconnect_start = time.time()
        log_with_timestamp("📱 Disconnecting...")
        await controller.disconnect()
        disconnect_elapsed = time.time() - disconnect_start
        log_with_timestamp(f"⏱️  Disconnect completed in {disconnect_elapsed:.1f}s")

        elapsed = time.time() - start_time
        log_with_timestamp(f"✅ Walk started successfully in {elapsed:.1f}s")

        return {"success": True, "message": "Walk started", "time": elapsed}

    except Exception as e:
        elapsed = time.time() - start_time
        log_with_timestamp(f"❌ Start walk failed after {elapsed:.1f}s: {e}")
        return {"success": False, "error": str(e), "time": elapsed}

async def main():
    """Main entry point"""
    try:
        address = load_config()
        result = await start_walking(address)
        return result
    except Exception as e:
        log_with_timestamp(f"Fatal error: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    result = asyncio.run(main())
    if not result["success"]:
        exit(1)
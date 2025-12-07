#!/usr/bin/env python3
"""
Stateless WalkingPad Start Script
Always: discover -> connect -> start -> disconnect
"""

import asyncio
import time
import yaml
import os
import json
from datetime import datetime
from ph4_walkingpad.pad import WalkingPad, Controller
from ph4_walkingpad.utils import setup_logging
from bleak import BleakScanner
from dotenv import load_dotenv
import sys

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


def reset_bleak_cache():
    """Force clear Bleak's internal BLE adapter state"""
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

async def ensure_advertising(address, timeout=3.0):
    """Quickly confirm the WalkingPad is advertising"""
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        return device is not None
    except Exception as exc:
        log_with_timestamp(f"⚠️  Advertising check failed: {exc}")
        return False


async def start_walking(address):
    """Complete start walking sequence with BLE reset fallback"""
    start_time = time.time()
    max_attempts = 2  # Try once, then reset and try again
    first_timeout = 4.0
    retry_timeout = 10.0
    command_pause = 0.05

    metrics = {
        "address": address,
        "attempts": [],
        "advertising": None,
    }

    advertising_present = await ensure_advertising(address, timeout=2.0)
    metrics["advertising"] = advertising_present
    log_metric("preflight", advertising=advertising_present)

    for attempt in range(max_attempts):
        try:
            attempt_no = attempt + 1
            attempt_record = {
                "attempt": attempt_no,
                "start_ts": time.time(),
                "timeout": first_timeout if attempt == 0 else retry_timeout,
            }
            if attempt > 0:
                log_with_timestamp(f"🔄 Attempt {attempt_no} after Bleak reset...")

            # Step 1: Connect directly to known address
            connect_start = time.time()
            log_with_timestamp(f"📱 Connecting directly to WalkingPad {address}...")
            controller = Controller()
            controller.log_messages_info = False
            timeout_seconds = first_timeout if attempt == 0 else retry_timeout
            await asyncio.wait_for(controller.run(address), timeout=timeout_seconds)
            connect_elapsed = time.time() - connect_start
            log_with_timestamp(f"⏱️  Connection completed in {connect_elapsed:.1f}s")
            attempt_record["connect_time"] = round(connect_elapsed, 2)
            attempt_record["status"] = "connected"
            metrics["attempts"].append(attempt_record)
            log_metric("connection", attempt=attempt_no, elapsed=connect_elapsed, timeout=timeout_seconds, status="connected")

            # Connection succeeded, break out of retry loop
            break

        except (asyncio.TimeoutError, Exception) as e:
            attempt_record = locals().get("attempt_record", {
                "attempt": attempt + 1,
                "start_ts": start_time,
            })
            if attempt == 0:  # First attempt failed
                error_name = type(e).__name__
                error_text = repr(e)
                attempt_record["status"] = "timeout"
                attempt_record["error_type"] = error_name
                attempt_record["error_text"] = error_text
                metrics["attempts"].append(attempt_record)
                log_with_timestamp(f"⚠️  First connection attempt failed: {error_text}")
                log_metric("connection", attempt=attempt_no, status="timeout", error=error_name)
                # Reset Bleak cache and try again
                reset_bleak_cache()
                await asyncio.sleep(0.5)  # Brief pause after reset
                try:
                    discovery_start = time.time()
                    await discover_walkingpad(address, timeout=6)
                    discovery_elapsed = time.time() - discovery_start
                    log_metric("discovery", found=True, elapsed=discovery_elapsed)
                except Exception as discover_error:
                    log_with_timestamp(f"⚠️  Quick discovery attempt failed: {discover_error}")
                    log_metric("discovery", found=False, error=type(discover_error).__name__)
                continue
            else:
                # Second attempt also failed
                elapsed = time.time() - start_time
                error_name = type(e).__name__
                error_text = repr(e)
                attempt_record["status"] = "failed"
                attempt_record["error_type"] = error_name
                attempt_record["error_text"] = error_text
                metrics["attempts"].append(attempt_record)
                log_with_timestamp(f"❌ Start walk failed after BLE reset attempt: {error_text}")
                log_metric("connection", attempt=attempt_no, status="failed", error=error_name, elapsed=elapsed)
                return {"success": False, "error": str(e), "time": elapsed, "metrics": metrics}

    try:

        # Step 3: Start sequence (optimized)
        sequence_start = time.time()
        log_with_timestamp("🚀 Starting walk sequence...")

        step_start = time.time()
        log_with_timestamp("  → Switching to MANUAL mode")
        await controller.switch_mode(WalkingPad.MODE_MANUAL)
        await asyncio.sleep(command_pause)  # Minimal delay for BLE command processing
        step_elapsed = time.time() - step_start
        log_with_timestamp(f"    ⏱️  MANUAL switch: {step_elapsed:.1f}s")

        step_start = time.time()
        log_with_timestamp("  → Starting belt")
        await controller.start_belt()
        await asyncio.sleep(command_pause)  # Minimal delay for BLE command processing
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
        log_metric("start_walk", success=True, total_time=elapsed, attempts=len(metrics["attempts"]))

        metrics["total_time"] = round(elapsed, 2)
        return {"success": True, "message": "Walk started", "time": elapsed, "metrics": metrics}

    except Exception as e:
        elapsed = time.time() - start_time
        log_with_timestamp(f"❌ Start walk failed after {elapsed:.1f}s: {e}")
        log_metric("start_walk", success=False, total_time=elapsed, error=type(e).__name__)
        metrics["total_time"] = round(elapsed, 2)
        return {"success": False, "error": str(e), "time": elapsed, "metrics": metrics}

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

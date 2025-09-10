#!/usr/bin/env python3
"""
Test script for the new connection manager
"""

import asyncio
import time
from datetime import datetime
from connection_manager import WalkingPadConnectionManager
import yaml

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # milliseconds
    print(f"[{timestamp}] {message}")

async def test_connection_manager():
    # Load config
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)
    
    print("🧪 Testing WalkingPad Connection Manager")
    print("=" * 50)
    
    manager = WalkingPadConnectionManager(config['address'])
    
    # Test 1: Manual scan
    print("\n1. Testing device scanning...")
    found = await manager.scan_for_device()
    if found:
        print("✅ Device found in scan")
        cache_info = manager.scan_cache.get(manager.address, {})
        print(f"   RSSI: {cache_info.get('rssi', 'N/A')}")
    else:
        print("❌ Device not found in scan")
    
    # Test 2: Connection with exponential backoff
    print("\n2. Testing connection with exponential backoff...")
    connected = await manager.connect_with_exponential_backoff(max_attempts=3)
    if connected:
        print("✅ Connected successfully")
        
        # Test 3: Basic operation
        print("\n3. Testing basic operation (ask_stats)...")
        try:
            await manager.controller.ask_stats()
            stats = manager.controller.last_status
            print(f"✅ Got stats: mode={stats.manual_mode}, belt_state={stats.belt_state}")
        except Exception as e:
            print(f"❌ Stats failed: {e}")
        
    else:
        print("❌ Connection failed")
    
    # Test 4: System state detection
    print("\n4. Testing system state detection...")
    power_connected = manager.check_power_connected()
    external_display = manager.check_external_display()
    should_connect = manager.should_attempt_connection()
    
    print(f"   Power connected: {power_connected}")
    print(f"   External display: {external_display}")
    print(f"   Should attempt connection: {should_connect}")
    
    # Clean up
    await manager.disconnect_safe()
    print("\n✅ Test completed")

if __name__ == "__main__":
    asyncio.run(test_connection_manager())
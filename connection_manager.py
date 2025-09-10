#!/usr/bin/env python3
"""
Connection Manager for WalkingPad BLE
Monitors power/display events and maintains persistent BLE connection
"""

import asyncio
import time
import subprocess
from datetime import datetime
from ph4_walkingpad.pad import WalkingPad, Controller
from ph4_walkingpad.utils import setup_logging
from bleak import BleakScanner
import threading
import os

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # milliseconds
    print(f"[{timestamp}] {message}")

class WalkingPadConnectionManager:
    def __init__(self, address):
        self.address = address
        self.controller = Controller()
        self.log = setup_logging()
        self.connected = False
        self.last_connection_attempt = 0
        self.last_health_check = 0
        self.connection_start_time = 0
        self.connection_lock = asyncio.Lock()
        self.monitoring_active = False
        self.monitor_thread = None
        self.scan_cache = {}
        self.scan_cache_timeout = 30  # seconds
        self.health_check_interval = 30  # seconds
        self.max_connection_age = 300  # 5 minutes max before reconnect
        
    async def scan_for_device(self):
        """Scan for WalkingPad device before attempting connection"""
        try:
            log_with_timestamp(f"Scanning for WalkingPad device {self.address}...")
            devices = await BleakScanner.discover(timeout=5.0)
            
            for device in devices:
                if device.address.upper() == self.address.upper():
                    self.scan_cache[self.address] = {
                        'device': device,
                        'timestamp': time.time(),
                        'rssi': device.rssi if hasattr(device, 'rssi') else None
                    }
                    log_with_timestamp(f"Found WalkingPad: {device.name} ({device.address}) RSSI: {getattr(device, 'rssi', 'N/A')}")
                    return True
            
            log_with_timestamp(f"WalkingPad {self.address} not found in scan")
            return False
            
        except Exception as e:
            log_with_timestamp(f"Scan failed: {e}")
            return False
    
    def is_scan_cache_valid(self):
        """Check if we have a recent scan result"""
        if self.address not in self.scan_cache:
            return False
        
        cache_age = time.time() - self.scan_cache[self.address]['timestamp']
        return cache_age < self.scan_cache_timeout
    
    async def connect_with_exponential_backoff(self, max_attempts=5):
        """Connect with exponential backoff strategy"""
        async with self.connection_lock:
            if self.connected:
                return True
                
            # Use cached scan result if available, otherwise scan first
            if not self.is_scan_cache_valid():
                if not await self.scan_for_device():
                    log_with_timestamp("Device not found in scan, skipping connection attempt")
                    return False
            
            base_delay = 0.5  # Start with 500ms
            max_delay = 8.0   # Cap at 8 seconds
            
            for attempt in range(max_attempts):
                try:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    
                    if attempt > 0:
                        log_with_timestamp(f"Waiting {delay:.1f}s before attempt {attempt + 1}")
                        await asyncio.sleep(delay)
                    
                    log_with_timestamp(f"Connection attempt {attempt + 1}/{max_attempts}")
                    await self.controller.run(self.address)
                    
                    # Test connection with a quick status request
                    await asyncio.sleep(0.1)
                    await self.controller.ask_stats()
                    
                    self.connected = True
                    self.last_connection_attempt = time.time()
                    self.connection_start_time = time.time()
                    log_with_timestamp("✅ Connected successfully with exponential backoff!")
                    return True
                    
                except Exception as e:
                    log_with_timestamp(f"Attempt {attempt + 1} failed: {e}")
                    if attempt == max_attempts - 1:
                        log_with_timestamp(f"❌ All {max_attempts} connection attempts failed")
                        
            return False
    
    async def disconnect_safe(self):
        """Safely disconnect"""
        async with self.connection_lock:
            if self.connected:
                try:
                    await self.controller.disconnect()
                    self.connected = False
                    log_with_timestamp("Disconnected safely")
                except Exception as e:
                    log_with_timestamp(f"Disconnect error: {e}")
                    self.connected = False
    
    def check_power_connected(self):
        """Check if laptop is connected to power (macOS)"""
        try:
            result = subprocess.run(['pmset', '-g', 'ps'], capture_output=True, text=True)
            return 'AC Power' in result.stdout
        except:
            return False
    
    def check_external_display(self):
        """Check if external display is connected (macOS)"""
        try:
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True)
            # Count displays - if more than 1, external display likely connected
            display_count = result.stdout.count('Resolution:')
            return display_count > 1
        except:
            return False
    
    def should_attempt_connection(self):
        """Determine if we should attempt connection based on system state"""
        power_connected = self.check_power_connected()
        external_display = self.check_external_display()
        
        # Don't attempt too frequently
        time_since_last = time.time() - self.last_connection_attempt
        if time_since_last < 30:  # Wait at least 30 seconds between attempts
            return False
            
        return power_connected or external_display
    
    def is_connection_stale(self):
        """Check if connection is too old and should be refreshed"""
        if not self.connected:
            return False
        
        connection_age = time.time() - self.connection_start_time
        return connection_age > self.max_connection_age
    
    def is_monitoring_thread_alive(self):
        """Check if monitoring thread is still alive"""
        return self.monitor_thread and self.monitor_thread.is_alive()
    
    async def health_check(self):
        """Perform connection health check"""
        try:
            current_time = time.time()
            
            # Don't check too frequently
            if current_time - self.last_health_check < self.health_check_interval:
                return True
            
            self.last_health_check = current_time
            
            if self.connected:
                # Test if connection is responsive
                await self.controller.ask_stats()
                
                # Check if connection is stale
                if self.is_connection_stale():
                    log_with_timestamp("Connection is stale, forcing reconnect")
                    self.connected = False
                    return False
                
                log_with_timestamp("Health check passed")
                return True
            
            return False
            
        except Exception as e:
            log_with_timestamp(f"Health check failed: {e}")
            self.connected = False
            return False
    
    async def monitor_and_connect(self):
        """Background task to monitor system events and maintain connection"""
        log_with_timestamp("🔍 Starting connection monitoring...")
        
        while self.monitoring_active:
            try:
                # Health check for existing connections
                if self.connected:
                    await self.health_check()
                
                # Attempt connection if not connected and conditions are right
                if not self.connected and self.should_attempt_connection():
                    log_with_timestamp("📱 Power/display detected - attempting WalkingPad connection...")
                    await self.connect_with_exponential_backoff()
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                log_with_timestamp(f"Monitor error: {e}")
                # Mark as disconnected on critical errors
                self.connected = False
                await asyncio.sleep(30)  # Wait longer on errors
    
    def start_monitoring(self):
        """Start the background monitoring with auto-recovery"""
        self.monitoring_active = True
        self._start_monitor_thread()
        log_with_timestamp("✅ Connection monitoring started")
    
    def _start_monitor_thread(self):
        """Start the actual monitoring thread"""
        def run_monitor():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.monitor_and_connect())
            except Exception as e:
                log_with_timestamp(f"Monitor thread crashed: {e}")
                # Auto-recovery: restart the thread after delay
                if self.monitoring_active:
                    log_with_timestamp("Attempting monitor thread auto-recovery...")
                    time.sleep(5)
                    self._start_monitor_thread()
        
        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop the background monitoring"""
        self.monitoring_active = False
        log_with_timestamp("⏹️  Connection monitoring stopped")
    
    async def get_connection(self, timeout=30):
        """Get a connection, attempting to connect if necessary with timeout"""
        start_time = time.time()
        
        # Check if we have a healthy connection
        if self.connected:
            try:
                # Quick health check to ensure connection is responsive
                await asyncio.wait_for(self.controller.ask_stats(), timeout=3.0)
                return self.controller
            except asyncio.TimeoutError:
                log_with_timestamp("Connection timeout during health check, marking as disconnected")
                self.connected = False
            except Exception as e:
                log_with_timestamp(f"Connection health check failed: {e}")
                self.connected = False
        
        # Attempt connection within timeout
        while time.time() - start_time < timeout:
            try:
                # Quick connection attempt if scan cache is valid
                if self.is_scan_cache_valid():
                    if await self.connect_with_exponential_backoff(max_attempts=2):
                        return self.controller
                
                # Fallback to scanning + connecting
                if await self.connect_with_exponential_backoff(max_attempts=3):
                    return self.controller
                    
                # Wait before retry
                await asyncio.sleep(2)
                
            except Exception as e:
                log_with_timestamp(f"Connection attempt failed: {e}")
                await asyncio.sleep(1)
        
        raise Exception(f"Unable to establish WalkingPad connection within {timeout}s timeout")


# Usage example/test
if __name__ == "__main__":
    import yaml
    
    # Load config
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)
    
    manager = WalkingPadConnectionManager(config['address'])
    
    # Start monitoring
    manager.start_monitoring()
    
    try:
        # Keep alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop_monitoring()
        print("Monitoring stopped")
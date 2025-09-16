#!/usr/bin/env python3
"""
Connection Manager for WalkingPad BLE
Monitors power/display events and maintains persistent BLE connection
"""

import asyncio
import time
import subprocess
import warnings
from datetime import datetime
from ph4_walkingpad.pad import WalkingPad, Controller
from ph4_walkingpad.utils import setup_logging
from bleak import BleakScanner
import threading
import os
import signal

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # milliseconds
    print(f"[{timestamp}] {message}")

def handle_unhandled_exception(loop, context):
    """Handle unhandled exceptions in async loop to prevent BleakError pileup"""
    exception = context.get('exception')
    if exception:
        error_msg = str(exception)
        if "disconnected" in error_msg.lower() or "bleak" in str(type(exception)).lower():
            # Suppress BleakError disconnected messages that are expected during sleep/wake
            pass
        else:
            log_with_timestamp(f"Unhandled async exception: {exception}")
    else:
        log_with_timestamp(f"Unhandled async context: {context}")

class WalkingPadConnectionManager:
    def __init__(self, address):
        self.address = address
        self.controller = Controller()
        # Reduce very chatty INFO logs from ph4_walkingpad on every BLE notification
        # to avoid filling launchd-managed stdout/stderr logs.
        self.controller.log_messages_info = False
        self.log = setup_logging()
        self.connected = False
        self.last_connection_attempt = 0
        self.last_health_check = 0
        self.connection_start_time = 0
        # Avoid simultaneous connect attempts; use threading lock since we have multiple loops
        # across threads (monitor thread + request loop)
        import threading as _threading
        self._connect_lock = _threading.Lock()
        self.monitoring_active = False
        self.monitor_thread = None
        self.scan_cache = {}
        self.scan_cache_timeout = 30  # seconds
        self.health_check_interval = 5  # seconds - more frequent for better sleep/wake detection
        self.max_connection_age = 180  # 3 minutes max before reconnect (reduced for stability)
        # Timeouts (seconds)
        self.ble_connect_timeout = 10.0
        self.ble_cmd_timeout = 5.0
        
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
        # Guard against concurrent connection attempts across threads/loops
        if not self._connect_lock.acquire(blocking=False):
            # Another attempt in progress; wait briefly and report current state
            await asyncio.sleep(0.1)
            return self.connected

        try:
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
                    # Bound BLE connect and probe with timeouts to avoid hanging
                    await asyncio.wait_for(self.controller.run(self.address), timeout=self.ble_connect_timeout)

                    # Test connection with a quick status request
                    await asyncio.sleep(0.1)
                    await asyncio.wait_for(self.controller.ask_stats(), timeout=self.ble_cmd_timeout)

                    self.connected = True
                    self.last_connection_attempt = time.time()
                    self.connection_start_time = time.time()
                    log_with_timestamp("✅ Connected successfully with exponential backoff!")
                    return True

                except asyncio.TimeoutError:
                    log_with_timestamp(f"Attempt {attempt + 1} timed out during BLE operation")
                    self.connected = False
                    # Best-effort disconnect to reset state
                    try:
                        await asyncio.wait_for(self.controller.disconnect(), timeout=2.0)
                    except Exception:
                        pass
                    if attempt == max_attempts - 1:
                        log_with_timestamp(f"❌ All {max_attempts} connection attempts timed out")
                except Exception as e:
                    # Handle BleakError and other BLE exceptions gracefully
                    error_msg = str(e)
                    if "disconnected" in error_msg.lower() or "bleak" in str(type(e)).lower():
                        log_with_timestamp(f"Attempt {attempt + 1} BLE disconnection: {error_msg}")
                    else:
                        log_with_timestamp(f"Attempt {attempt + 1} failed: {e}")

                    self.connected = False
                    # Clean disconnect on any error with proper async cleanup
                    try:
                        await asyncio.wait_for(self.controller.disconnect(), timeout=1.0)
                    except Exception:
                        # Force reset controller state to prevent lingering futures
                        if hasattr(self.controller, '_client'):
                            self.controller._client = None
                        if hasattr(self.controller, '_device'):
                            self.controller._device = None

                    if attempt == max_attempts - 1:
                        log_with_timestamp(f"❌ All {max_attempts} connection attempts failed")

            return False
        finally:
            try:
                self._connect_lock.release()
            except Exception:
                pass
    
    async def disconnect_safe(self):
        """Safely disconnect and COMPLETELY reset controller"""
        if self.connected:
            try:
                # Force cleanup of controller internal state first
                if hasattr(self.controller, '_client') and self.controller._client:
                    try:
                        # Clean disconnect
                        await asyncio.wait_for(self.controller.disconnect(), timeout=2.0)
                    except Exception:
                        pass  # Ignore disconnect errors

                self.connected = False
                log_with_timestamp("Disconnected safely - recreating controller")
            except Exception as e:
                error_msg = str(e)
                if "disconnected" in error_msg.lower() or "bleak" in str(type(e)).lower():
                    log_with_timestamp(f"Disconnect BLE error (expected): {error_msg}")
                else:
                    log_with_timestamp(f"Disconnect error: {e}")
                self.connected = False

        # NUCLEAR RESET: Create completely fresh controller
        log_with_timestamp("Creating fresh controller instance")
        old_controller = self.controller
        self.controller = Controller()
        self.controller.log_messages_info = False

        # Clean up old controller
        try:
            if hasattr(old_controller, '_client'):
                old_controller._client = None
            if hasattr(old_controller, '_device'):
                old_controller._device = None
            del old_controller
        except Exception:
            pass
    
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
        """Always attempt connection - we want to maintain it whenever possible"""
        # Don't attempt too frequently to avoid spamming
        time_since_last = time.time() - self.last_connection_attempt
        if time_since_last < 5:  # Reduced to 5 seconds for more responsive connection
            return False

        return True  # Always try to connect when not connected
    
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
        """Perform connection health check with aggressive sleep/wake detection"""
        try:
            current_time = time.time()

            # Don't check too frequently
            if current_time - self.last_health_check < self.health_check_interval:
                return True

            self.last_health_check = current_time

            if self.connected:
                # Test if connection is responsive with shorter timeout for sleep detection
                try:
                    await asyncio.wait_for(self.controller.ask_stats(), timeout=2.0)
                except asyncio.TimeoutError:
                    log_with_timestamp("Health check timeout - validating if device is still discoverable")
                    # Double-check by scanning - if device is discoverable but connection fails, force reset
                    if await self.scan_for_device():
                        log_with_timestamp("Device found in scan but connection unresponsive - forcing reset")
                        await self.disconnect_safe()
                        self.connected = False
                        return False
                    else:
                        log_with_timestamp("Device not discoverable - will keep trying to reconnect")
                        self.connected = False
                        return False
                except Exception as e:
                    error_msg = str(e)
                    if "disconnected" in error_msg.lower() or "bleak" in str(type(e)).lower():
                        log_with_timestamp("Health check detected BLE disconnection - forcing clean reset")
                        await self.disconnect_safe()
                    else:
                        log_with_timestamp(f"Health check connection error: {e}")
                    self.connected = False
                    return False

                # Check if connection is stale
                if self.is_connection_stale():
                    log_with_timestamp("Connection is stale, forcing reconnect")
                    await self.disconnect_safe()
                    self.connected = False
                    return False

                return True

            return False

        except Exception as e:
            error_msg = str(e)
            if "disconnected" in error_msg.lower() or "bleak" in str(type(e)).lower():
                log_with_timestamp(f"Health check BLE disconnection (sleep/wake): {error_msg}")
            else:
                log_with_timestamp(f"Health check failed: {e}")
            self.connected = False
            return False
    
    async def monitor_and_connect(self):
        """Background task to continuously scan for and maintain WalkingPad connection"""
        log_with_timestamp("🔍 Starting always-on WalkingPad monitoring...")

        while self.monitoring_active:
            try:
                # Health check for existing connections
                if self.connected:
                    health_ok = await self.health_check()
                    if not health_ok:
                        log_with_timestamp("Health check failed, performing cleanup and reconnection")
                        self.connected = False
                        # Force disconnect with proper state cleanup
                        await self.disconnect_safe()

                # Always attempt connection if not connected (continuous scanning strategy)
                if not self.connected and self.should_attempt_connection():
                    log_with_timestamp("🔄 Continuously scanning for WalkingPad...")
                    success = await self.connect_with_exponential_backoff()
                    if success:
                        log_with_timestamp("✅ WalkingPad connection established!")
                    else:
                        log_with_timestamp("❌ Connection failed, will retry soon")

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                log_with_timestamp(f"Monitor error: {e}")
                # Mark as disconnected on critical errors
                self.connected = False
                await asyncio.sleep(15)  # Shorter wait on errors for faster recovery
    
    def start_monitoring(self):
        """Start the background monitoring with auto-recovery"""
        self.monitoring_active = True
        self._start_monitor_thread()
        log_with_timestamp("✅ Connection monitoring started")
    
    def _start_monitor_thread(self):
        """Start the actual monitoring thread with unhandled exception handler"""
        def run_monitor():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # Set exception handler to suppress BleakError disconnected spam
                loop.set_exception_handler(handle_unhandled_exception)
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
                await asyncio.wait_for(self.controller.ask_stats(), timeout=self.ble_cmd_timeout)
                return self.controller
            except asyncio.TimeoutError:
                log_with_timestamp("Connection timeout during health check, marking as disconnected")
                self.connected = False
            except Exception as e:
                error_msg = str(e)
                if "disconnected" in error_msg.lower() or "bleak" in str(type(e)).lower():
                    log_with_timestamp(f"Connection health check BLE disconnection: {error_msg}")
                else:
                    log_with_timestamp(f"Connection health check failed: {e}")
                self.connected = False
        
        # Attempt connection within timeout
        while time.time() - start_time < timeout:
            try:
                # Quick connection attempt if scan cache is valid
                if self.is_scan_cache_valid():
                    try:
                        remaining = max(1.0, timeout - (time.time() - start_time))
                        if await asyncio.wait_for(self.connect_with_exponential_backoff(max_attempts=2), timeout=remaining):
                            return self.controller
                    except asyncio.TimeoutError:
                        log_with_timestamp("Timed out during fast connect attempts")
                        pass
                
                # Fallback to scanning + connecting
                try:
                    remaining = max(1.0, timeout - (time.time() - start_time))
                    if await asyncio.wait_for(self.connect_with_exponential_backoff(max_attempts=3), timeout=remaining):
                        return self.controller
                except asyncio.TimeoutError:
                    log_with_timestamp("Timed out during connect attempts")
                    
                # Wait before retry
                await asyncio.sleep(2)
                
            except Exception as e:
                error_msg = str(e)
                if "disconnected" in error_msg.lower() or "bleak" in str(type(e)).lower():
                    log_with_timestamp(f"Connection attempt BLE disconnection: {error_msg}")
                else:
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

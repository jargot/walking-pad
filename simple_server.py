#!/usr/bin/env python3
"""
Simple Stateless WalkingPad Server
Calls individual scripts instead of maintaining persistent connections
"""

import asyncio
import subprocess
import json
import os
import signal
import sys
import threading
from flask import Flask, request, jsonify
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Prevent concurrent BLE operations (BLE only supports one connection at a time)
_ble_lock = threading.Lock()

# Debounce: track last successful operation to ignore duplicate requests
_last_success = {}  # {"startwalk": datetime, "save_and_stop": datetime}
DEBOUNCE_SECONDS = 5

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {message}")

def extract_metrics(lines):
    """Return parsed metric dictionaries from stdout lines"""
    metrics = []
    for line in lines:
        if line.startswith('[METRIC] '):
            payload = line[len('[METRIC] '):]
            try:
                metrics.append(json.loads(payload))
            except json.JSONDecodeError as exc:
                log_with_timestamp(f"⚠️  Failed to parse metric line: {exc}" )
    return metrics


def run_script(script_name):
    """Run a WalkingPad script and return the result (single attempt)"""
    start_time = datetime.now()
    log_with_timestamp(f"🏃 Running {script_name}...")

    try:
        # Use Popen so we can send SIGINT on timeout (not SIGKILL).
        # SIGINT lets asyncio.run() cancel tasks and run finally blocks,
        # which disconnect BLE cleanly instead of orphaning the connection.
        proc = subprocess.Popen(
            ["uv", "run", "python", script_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # Own process group for clean signal delivery
        )

        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            log_with_timestamp(f"⏱️  {script_name} timed out, sending SIGINT for graceful BLE disconnect...")
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            try:
                stdout, stderr = proc.communicate(timeout=5)
                log_with_timestamp(f"⏱️  {script_name} shut down gracefully after SIGINT")
            except subprocess.TimeoutExpired:
                log_with_timestamp(f"⏱️  {script_name} didn't exit after SIGINT, sending SIGKILL...")
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.communicate()

            elapsed = (datetime.now() - start_time).total_seconds()
            return {
                "success": False,
                "error": f"Script timed out after {elapsed:.0f} seconds",
                "elapsed": elapsed
            }

        elapsed = (datetime.now() - start_time).total_seconds()
        stdout_lines = stdout.strip().split('\n') if stdout and stdout.strip() else []
        metrics = extract_metrics(stdout_lines)

        if metrics:
            for metric in metrics:
                log_with_timestamp(f"📈 Metric {metric.get('event')}: {metric}")

        if proc.returncode == 0:
            log_with_timestamp(f"✅ {script_name} completed successfully in {elapsed:.1f}s")
            return {
                "success": True,
                "output": stdout,
                "elapsed": elapsed,
                "logs": stdout_lines,
                "metrics": metrics
            }
        else:
            err_msg = (stderr or "").strip()
            if not err_msg:
                last_line = ""
                for line in stdout_lines:
                    if line.strip():
                        last_line = line
                err_msg = last_line

            log_with_timestamp(f"❌ {script_name} failed in {elapsed:.1f}s: {err_msg}")
            return {
                "success": False,
                "error": err_msg,
                "output": stdout,
                "elapsed": elapsed,
                "logs": stdout_lines,
                "metrics": metrics
            }

    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        log_with_timestamp(f"💥 {script_name} crashed in {elapsed:.1f}s: {e}")
        return {
            "success": False,
            "error": str(e),
            "elapsed": elapsed
        }

def run_script_with_retries(script_name, max_retries=3):
    """Run a WalkingPad script with server-level retries"""
    overall_start_time = datetime.now()
    log_with_timestamp(f"🔄 Starting {script_name} with up to {max_retries} retries...")

    all_metrics = []
    last_result = None

    for attempt in range(max_retries):
        attempt_num = attempt + 1
        log_with_timestamp(f"🎯 Attempt {attempt_num}/{max_retries} for {script_name}")

        result = run_script(script_name)
        last_result = result

        # Collect metrics from all attempts
        if result.get("metrics"):
            all_metrics.extend(result["metrics"])

        if result["success"]:
            overall_elapsed = (datetime.now() - overall_start_time).total_seconds()
            log_with_timestamp(f"🎉 {script_name} succeeded on attempt {attempt_num} (total: {overall_elapsed:.1f}s)")

            # Return success with combined metrics
            return {
                "success": True,
                "output": result["output"],
                "elapsed": overall_elapsed,
                "logs": result.get("logs", []),
                "metrics": all_metrics,
                "attempts": attempt_num
            }
        else:
            # Log the failure but don't give up yet (unless it's the last attempt)
            if attempt_num < max_retries:
                retry_delay = 2 * attempt_num  # 2s, 4s delays
                log_with_timestamp(f"⚠️  Attempt {attempt_num} failed: {result.get('error', 'Unknown error')}")
                log_with_timestamp(f"⏳ Waiting {retry_delay}s before retry...")
                import time
                time.sleep(retry_delay)
            else:
                log_with_timestamp(f"💀 All {max_retries} attempts failed for {script_name}")

    # All retries failed - return the last result with combined metrics
    overall_elapsed = (datetime.now() - overall_start_time).total_seconds()
    return {
        "success": False,
        "error": last_result.get("error", "All retry attempts failed"),
        "output": last_result.get("output", ""),
        "elapsed": overall_elapsed,
        "logs": last_result.get("logs", []),
        "metrics": all_metrics,
        "attempts": max_retries
    }

@app.route("/startwalk", methods=['POST'])
def start_walk():
    """Start walking by calling the stateless script with retries"""
    last = _last_success.get("startwalk")
    if last and (datetime.now() - last).total_seconds() < DEBOUNCE_SECONDS:
        log_with_timestamp("⚠️  Debounced /startwalk — already started recently")
        return jsonify({"message": "Walk already started", "debounced": True}), 200
    if not _ble_lock.acquire(blocking=False):
        log_with_timestamp("⚠️  Rejected /startwalk — another BLE operation in progress")
        return jsonify({"error": "Another BLE operation is in progress. Please wait."}), 409
    try:
        log_with_timestamp("📥 Received start walk request")
        result = run_script_with_retries("start_walk.py", max_retries=3)

        response_payload = {
            "elapsed": result.get("elapsed", 0),
            "metrics": result.get("metrics", []),
            "attempts": result.get("attempts", 1)
        }

        if result["success"]:
            _last_success["startwalk"] = datetime.now()
            _last_success.pop("save_and_stop", None)  # Clear stop debounce on new start
            response_payload["message"] = "Walk started successfully"
            return jsonify(response_payload), 200
        else:
            response_payload["error"] = result.get("error", "")
            return jsonify(response_payload), 500
    finally:
        _ble_lock.release()

@app.route("/save_and_stop", methods=['POST'])
def save_and_stop():
    """Stop walking and save to database by calling the stateless script with retries"""
    last = _last_success.get("save_and_stop")
    if last and (datetime.now() - last).total_seconds() < DEBOUNCE_SECONDS:
        log_with_timestamp("⚠️  Debounced /save_and_stop — already stopped recently")
        return jsonify({"message": "Walk already stopped", "debounced": True}), 200
    if not _ble_lock.acquire(blocking=False):
        log_with_timestamp("⚠️  Rejected /save_and_stop — another BLE operation in progress")
        return jsonify({"error": "Another BLE operation is in progress. Please wait."}), 409
    try:
        log_with_timestamp("📥 Received save and stop request")
        result = run_script_with_retries("stop_walk.py", max_retries=3)

        response_payload = {
            "elapsed": result.get("elapsed", 0),
            "metrics": result.get("metrics", []),
            "output": result.get("output", ""),
            "attempts": result.get("attempts", 1)
        }

        if result["success"]:
            _last_success["save_and_stop"] = datetime.now()
            _last_success.pop("startwalk", None)  # Clear start debounce on stop
            response_payload["message"] = "Walk stopped and saved successfully"
            return jsonify(response_payload), 200
        else:
            response_payload["error"] = result.get("error", "")
            return jsonify(response_payload), 500
    finally:
        _ble_lock.release()

@app.route("/status", methods=['GET'])
def status():
    """Simple status endpoint"""
    return jsonify({
        "status": "Simple stateless WalkingPad server",
        "approach": "discover-connect-command-disconnect",
        "version": "2.0-stateless"
    }), 200

@app.route("/health", methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200

# Legacy endpoints for compatibility
@app.route("/finishwalk", methods=['POST'])
def finish_walk():
    """Legacy endpoint - redirects to save_and_stop"""
    return save_and_stop()

if __name__ == '__main__':
    log_with_timestamp("🚀 Starting Simple Stateless WalkingPad Server")
    log_with_timestamp("📋 Approach: Always discover → connect → command → disconnect")

    # Check if scripts exist
    for script in ["start_walk.py", "stop_walk.py"]:
        if not os.path.exists(script):
            log_with_timestamp(f"❌ Missing script: {script}")
            sys.exit(1)

    log_with_timestamp("✅ All scripts found")
    app.run(debug=False, use_reloader=False, host='0.0.0.0', port=5678, processes=1, threaded=True)

#!/usr/bin/env python3
"""
Simple Stateless WalkingPad Server
Calls individual scripts instead of maintaining persistent connections
"""

import asyncio
import subprocess
import json
import os
import sys
from flask import Flask, request, jsonify
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

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
    """Run a WalkingPad script and return the result"""
    start_time = datetime.now()
    log_with_timestamp(f"🏃 Running {script_name}...")

    try:
        # Run the script using uv
        result = subprocess.run(
            ["uv", "run", "python", script_name],
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout
        )

        elapsed = (datetime.now() - start_time).total_seconds()

        stdout_lines = result.stdout.strip().split('\n') if result.stdout else []
        metrics = extract_metrics(stdout_lines)

        if metrics:
            for metric in metrics:
                log_with_timestamp(f"📈 Metric {metric.get('event')}: {metric}")

        if result.returncode == 0:
            log_with_timestamp(f"✅ {script_name} completed successfully in {elapsed:.1f}s")
            return {
                "success": True,
                "output": result.stdout,
                "elapsed": elapsed,
                "logs": stdout_lines,
                "metrics": metrics
            }
        else:
            # If stderr is empty, fall back to the last non-empty stdout line
            err_msg = result.stderr.strip()
            if not err_msg:
                last_line = ""
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        last_line = line
                err_msg = last_line

            log_with_timestamp(f"❌ {script_name} failed in {elapsed:.1f}s: {err_msg}")
            return {
                "success": False,
                "error": err_msg,
                "output": result.stdout,
                "elapsed": elapsed,
                "logs": stdout_lines,
                "metrics": metrics
            }

    except subprocess.TimeoutExpired:
        log_with_timestamp(f"⏱️  {script_name} timed out after 30s")
        return {
            "success": False,
            "error": "Script timed out after 30 seconds",
            "elapsed": 30.0
        }
    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        log_with_timestamp(f"💥 {script_name} crashed in {elapsed:.1f}s: {e}")
        return {
            "success": False,
            "error": str(e),
            "elapsed": elapsed
        }

@app.route("/startwalk", methods=['POST'])
def start_walk():
    """Start walking by calling the stateless script"""
    log_with_timestamp("📥 Received start walk request")
    result = run_script("start_walk.py")

    response_payload = {
        "elapsed": result.get("elapsed", 0),
        "metrics": result.get("metrics", []),
    }

    if result["success"]:
        response_payload["message"] = "Walk started successfully"
        return jsonify(response_payload), 200
    else:
        response_payload["error"] = result.get("error", "")
        return jsonify(response_payload), 500

@app.route("/save_and_stop", methods=['POST'])
def save_and_stop():
    """Stop walking and save to database by calling the stateless script"""
    log_with_timestamp("📥 Received save and stop request")
    result = run_script("stop_walk.py")

    response_payload = {
        "elapsed": result.get("elapsed", 0),
        "metrics": result.get("metrics", []),
        "output": result.get("output", "")
    }

    if result["success"]:
        response_payload["message"] = "Walk stopped and saved successfully"
        return jsonify(response_payload), 200
    else:
        # Return output logs too to help diagnose failures under launchd
        response_payload["error"] = result.get("error", "")
        return jsonify(response_payload), 500

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

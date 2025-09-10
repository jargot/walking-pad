from flask import Flask, request
from ph4_walkingpad import pad
from ph4_walkingpad.pad import WalkingPad, Controller
from ph4_walkingpad.utils import setup_logging
import asyncio
import yaml
import psycopg2
from datetime import datetime
import os
from dotenv import load_dotenv
import time
from functools import wraps
from connection_manager import WalkingPadConnectionManager

def log_with_timestamp(message):
    """Print message with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # milliseconds
    print(f"[{timestamp}] {message}")

load_dotenv()

app = Flask(__name__)

# minimal_cmd_space does not exist in the version we use from pip, thus we define it here.
# This should be removed once we can take it from the controller
minimal_cmd_space = 0.69

log = setup_logging()
pad.logger = log

# Initialize connection manager
connection_manager = None

last_status = {
    "steps": None,
    "distance": None,
    "time": None
}


def on_new_status(sender, record):

    distance_in_km = record.dist / 100
    log_with_timestamp("Received Record:")
    log_with_timestamp('Distance: {0}km'.format(distance_in_km))
    log_with_timestamp('Time: {0} seconds'.format(record.time))
    log_with_timestamp('Steps: {0}'.format(record.steps))

    last_status['steps'] = record.steps
    last_status['distance'] = distance_in_km
    last_status['time'] = record.time


def store_in_db(steps, distance_in_km, duration_in_seconds):
    db_host = os.getenv('DB_HOST')
    if not db_host:
        return

    conn = None
    cur = None
    try:
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

    except Exception as e:
        log_with_timestamp(f"Database error: {e}")
        if conn:
            conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def load_config():
    # Load from environment variables first, fallback to config.yaml
    walkingpad_address = os.getenv('WALKINGPAD_ADDRESS')
    
    if walkingpad_address:
        return {
            'address': walkingpad_address,
            'database': {
                'host': os.getenv('DB_HOST'),
                'port': int(os.getenv('DB_PORT', 5432)),
                'dbname': os.getenv('DB_NAME'),
                'user': os.getenv('DB_USER'),
                'password': os.getenv('DB_PASSWORD')
            }
        }
    
    # Fallback to config.yaml
    with open("config.yaml", 'r') as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)


def save_config(config):
    with open('config.yaml', 'w') as outfile:
        yaml.dump(config, outfile, default_flow_style=False)


def initialize_connection_manager():
    """Initialize the connection manager"""
    global connection_manager
    if connection_manager is None:
        config = load_config()
        connection_manager = WalkingPadConnectionManager(config['address'])
        connection_manager.start_monitoring()


def ble_operation(func):
    """Decorator for BLE operations using connection manager"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        global connection_manager
        initialize_connection_manager()
        
        try:
            # Use the already-connected controller if available
            if connection_manager.connected:
                log_with_timestamp(f"Using existing connection for {func.__name__}")
                ctler = connection_manager.controller
            else:
                log_with_timestamp(f"Establishing connection for {func.__name__}")
                ctler = await connection_manager.get_connection()
            
            # Execute the operation
            result = await func(ctler, *args, **kwargs)
            return result
            
        except Exception as e:
            log_with_timestamp(f"BLE operation failed: {e}")
            return {"error": str(e)}, 500
    return wrapper


@app.route("/config/address", methods=['GET'])
def get_config_address():
    config = load_config()
    return str(config['address']), 200


@app.route("/config/address", methods=['POST'])
def set_config_address():
    address = request.args.get('address')
    config = load_config()
    config['address'] = address
    save_config(config)

    return get_config_address()


@app.route("/mode", methods=['GET'])
@ble_operation
async def get_pad_mode(ctler):
    await ctler.ask_stats()
    await asyncio.sleep(minimal_cmd_space)
    stats = ctler.last_status
    mode = stats.manual_mode

    if (mode == WalkingPad.MODE_STANDBY):
        return "standby"
    elif (mode == WalkingPad.MODE_MANUAL):
        return "manual"
    elif (mode == WalkingPad.MODE_AUTOMAT):
        return "auto"
    else:
        return "Mode {0} not supported".format(mode), 400

@app.route("/mode", methods=['POST'])
@ble_operation
async def change_pad_mode(ctler):
    new_mode = request.args.get('new_mode')
    log_with_timestamp("Got mode {0}".format(new_mode))

    if (new_mode.lower() == "standby"):
        pad_mode = WalkingPad.MODE_STANDBY
    elif (new_mode.lower() == "manual"):
        pad_mode = WalkingPad.MODE_MANUAL
    elif (new_mode.lower() == "auto"):
        pad_mode = WalkingPad.MODE_AUTOMAT
    else:
        return "Mode {0} not supported".format(new_mode), 400

    await ctler.switch_mode(pad_mode)
    await asyncio.sleep(minimal_cmd_space)
    return new_mode

@app.route("/status", methods=['GET'])
@ble_operation
async def get_status(ctler):
    await ctler.ask_stats()
    await asyncio.sleep(minimal_cmd_space)
    stats = ctler.last_status
    mode = stats.manual_mode
    belt_state = stats.belt_state

    if (mode == WalkingPad.MODE_STANDBY):
        mode = "standby"
    elif (mode == WalkingPad.MODE_MANUAL):
        mode = "manual"
    elif (mode == WalkingPad.MODE_AUTOMAT):
        mode = "auto"

    if (belt_state == 5):
        belt_state = "standby"
    elif (belt_state == 0):
        belt_state = "idle"
    elif (belt_state == 1):
        belt_state = "running"
    elif (belt_state >=7):
        belt_state = "starting"

    dist = stats.dist / 100
    time = stats.time
    steps = stats.steps
    speed = stats.speed / 10

    return { "dist": dist, "time": time, "steps": steps, "speed": speed, "belt_state": belt_state }


@app.route("/history", methods=['GET'])
@ble_operation
async def get_history(ctler):
    await ctler.ask_hist(0)
    await asyncio.sleep(minimal_cmd_space)
    return last_status

@app.route("/save", methods=['POST'])
def save():
    store_in_db(last_status['steps'], last_status['distance'], last_status['time'])

@app.route("/startwalk", methods=['POST'])
@ble_operation
async def start_walk(ctler):
    log_with_timestamp("🚀 Starting walk sequence...")
    
    # Ensure handler is set up
    ctler.handler_last_status = on_new_status
    
    log_with_timestamp("Step 1: Switching to STANDBY mode")
    await ctler.switch_mode(WalkingPad.MODE_STANDBY) # Ensure we start from a known state, since start_belt is actually toggle_belt
    await asyncio.sleep(minimal_cmd_space)
    
    log_with_timestamp("Step 2: Switching to MANUAL mode")  
    await ctler.switch_mode(WalkingPad.MODE_MANUAL)
    await asyncio.sleep(minimal_cmd_space)
    
    log_with_timestamp("Step 3: Starting belt")
    await ctler.start_belt()
    await asyncio.sleep(minimal_cmd_space)
    
    log_with_timestamp("Step 4: Asking for history")
    await ctler.ask_hist(1)
    await asyncio.sleep(minimal_cmd_space)
    
    log_with_timestamp("✅ Walk start sequence completed")
    return last_status

@app.route("/finishwalk", methods=['POST'])
@ble_operation
async def finish_walk(ctler):
    await ctler.switch_mode(WalkingPad.MODE_STANDBY)
    await asyncio.sleep(minimal_cmd_space)
    await ctler.ask_hist(1)
    await asyncio.sleep(minimal_cmd_space)
    store_in_db(last_status['steps'], last_status['distance'], last_status['time'])
    return last_status

@app.route("/save_and_stop", methods=['POST'])
@ble_operation
async def save_and_stop(ctler):
    await ctler.ask_stats()
    await asyncio.sleep(minimal_cmd_space)
    stats = ctler.last_status
    mode = stats.manual_mode
    belt_state = stats.belt_state

    if (mode == WalkingPad.MODE_STANDBY):
        mode = "standby"
    elif (mode == WalkingPad.MODE_MANUAL):
        mode = "manual"
    elif (mode == WalkingPad.MODE_AUTOMAT):
        mode = "auto"

    if (belt_state == 5):
        belt_state = "standby"
    elif (belt_state == 0):
        belt_state = "idle"
    elif (belt_state == 1):
        belt_state = "running"
    elif (belt_state >=7):
        belt_state = "starting"

    dist = stats.dist / 100
    time = stats.time
    steps = stats.steps
    speed = stats.speed / 10

    store_in_db(steps=steps, distance_in_km=dist, duration_in_seconds=time)

    await ctler.switch_mode(WalkingPad.MODE_STANDBY)
    await asyncio.sleep(minimal_cmd_space)

    return last_status


def setup_handlers():
    """Setup handlers after connection manager is initialized"""
    global connection_manager
    initialize_connection_manager()
    if connection_manager and connection_manager.controller:
        connection_manager.controller.handler_last_status = on_new_status

if __name__ == '__main__':
    setup_handlers()
    app.run(debug=True, host='0.0.0.0', port=5678, processes=1, threaded=False)

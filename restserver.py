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

load_dotenv()

app = Flask(__name__)

# minimal_cmd_space does not exist in the version we use from pip, thus we define it here.
# This should be removed once we can take it from the controller
minimal_cmd_space = 0.69

log = setup_logging()
pad.logger = log
ctler = Controller()

last_status = {
    "steps": None,
    "distance": None,
    "time": None
}


def on_new_status(sender, record):

    distance_in_km = record.dist / 100
    print("Received Record:")
    print('Distance: {0}km'.format(distance_in_km))
    print('Time: {0} seconds'.format(record.time))
    print('Steps: {0}'.format(record.steps))

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
        print(f"Database error: {e}")
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


async def connect_with_retry(max_retries=3):
    """Connect to WalkingPad with retry logic"""
    address = load_config()['address']
    
    for attempt in range(max_retries):
        try:
            print(f"Connecting to {address} (attempt {attempt + 1}/{max_retries})")
            await ctler.run(address)
            await asyncio.sleep(minimal_cmd_space)
            print("Connected successfully")
            return True
        except Exception as e:
            print(f"Connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)  # Wait before retry
            else:
                raise Exception(f"Failed to connect after {max_retries} attempts")
    return False


async def disconnect_safe():
    """Safely disconnect with error handling"""
    try:
        await ctler.disconnect()
        await asyncio.sleep(minimal_cmd_space)
        print("Disconnected successfully")
    except Exception as e:
        print(f"Disconnect error (non-fatal): {e}")


def ble_operation(func):
    """Decorator for BLE operations with error handling"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            await connect_with_retry()
            return await func(*args, **kwargs)
        except Exception as e:
            print(f"BLE operation failed: {e}")
            return {"error": str(e)}, 500
        finally:
            await disconnect_safe()
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
async def get_pad_mode():
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
async def change_pad_mode():
    new_mode = request.args.get('new_mode')
    print("Got mode {0}".format(new_mode))

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
async def get_status():
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
async def get_history():
    await ctler.ask_hist(0)
    await asyncio.sleep(minimal_cmd_space)
    return last_status

@app.route("/save", methods=['POST'])
def save():
    store_in_db(last_status['steps'], last_status['distance'], last_status['time'])

@app.route("/startwalk", methods=['POST'])
@ble_operation
async def start_walk():
    await ctler.switch_mode(WalkingPad.MODE_STANDBY) # Ensure we start from a known state, since start_belt is actually toggle_belt
    await asyncio.sleep(minimal_cmd_space)
    await ctler.switch_mode(WalkingPad.MODE_MANUAL)
    await asyncio.sleep(minimal_cmd_space)
    await ctler.start_belt()
    await asyncio.sleep(minimal_cmd_space)
    await ctler.ask_hist(1)
    await asyncio.sleep(minimal_cmd_space)
    return last_status

@app.route("/finishwalk", methods=['POST'])
@ble_operation
async def finish_walk():
    await ctler.switch_mode(WalkingPad.MODE_STANDBY)
    await asyncio.sleep(minimal_cmd_space)
    await ctler.ask_hist(1)
    await asyncio.sleep(minimal_cmd_space)
    store_in_db(last_status['steps'], last_status['distance'], last_status['time'])
    return last_status

@app.route("/save_and_stop", methods=['POST'])
@ble_operation
async def save_and_stop():
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


ctler.handler_last_status = on_new_status

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5678, processes=1, threaded=False)

from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS
import threading
import time
import csv
from datetime import datetime
import os
import obd

# ===== CONFIG =====
OBD_PORT = "socket://192.168.0.10:35000"   # your adapter
OBD_TIMEOUT = 5
LOG_INTERVAL = 0.1          # seconds (10 Hz)
CSV_BUFFER_FLUSH = 10       # flush to disk every N rows
CSV_DIR = "./logs"
if not os.path.exists(CSV_DIR):
    os.makedirs(CSV_DIR)

# ===== GLOBAL STATE =====
app = Flask(__name__)
CORS(app)

state_lock = threading.Lock()
state = {
    "connected": False,
    "is_logging": False,
    "log_start_time": None,      # ISO string
    "last_update": None,         # ISO string
    "total_records": 0,
    "last_values": {
        "rpm": 0,
        "speed": 0,
        "coolant": 0,
        "oil": "N/A"
    },
    "csv_file": None,            # current csv filename
    "csv_path": None
}

# Logging thread control
_log_thread = None
_stop_event = threading.Event()

# OBD connection (single shared connection)
obd_conn = None

# ===== OBD SETUP =====
def init_obd():
    global obd_conn
    try:
        obd_conn = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=OBD_TIMEOUT, protocol="6")
        with state_lock:
            state["connected"] = obd_conn.is_connected()
    except Exception as e:
        with state_lock:
            state["connected"] = False
        print("OBD init error:", e)

# Call at import
init_obd()

# ===== CSV HELPERS =====
def new_csv_file():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"obd_log_{ts}.csv"
    path = os.path.join(CSV_DIR, fname)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "rpm", "speed_kmh", "coolant_c", "oil_c"])
    return fname, path

# ===== LOGGER THREAD =====
def logger_thread():
    """Background thread that polls OBD and writes to CSV."""
    global obd_conn
    buffer_rows = []
    rows_since_flush = 0

    # Commands (standard)
    cmd_rpm = obd.commands.RPM
    cmd_speed = obd.commands.SPEED
    cmd_cool = obd.commands.COOLANT_TEMP
    cmd_oil = obd.commands.OIL_TEMP

    while not _stop_event.is_set():
        start = time.time()

        if obd_conn is None or not obd_conn.is_connected():
            # Try to re-init connection intermittently
            try:
                init_obd()
            except Exception:
                pass

        # Default values are last-good values
        with state_lock:
            last_vals = state["last_values"].copy()

        try:
            # Query with force=True to reduce caching delays
            rpm_r = obd_conn.query(cmd_rpm, force=True) if obd_conn and obd_conn.is_connected() else None
            spd_r = obd_conn.query(cmd_speed, force=True) if obd_conn and obd_conn.is_connected() else None
            cool_r = obd_conn.query(cmd_cool, force=True) if obd_conn and obd_conn.is_connected() else None
            oil_r = obd_conn.query(cmd_oil, force=True) if obd_conn and obd_conn.is_connected() else None
        except Exception as e:
            # On communication errors, mark disconnected (dashboard will show)
            with state_lock:
                state["connected"] = False
            rpm_r = spd_r = cool_r = oil_r = None

        # Update last-good values
        with state_lock:
            # RPM
            if rpm_r and (not rpm_r.is_null()):
                try:
                    last_vals["rpm"] = rpm_r.value.magnitude
                except Exception:
                    pass

            # Speed
            if spd_r and (not spd_r.is_null()):
                try:
                    last_vals["speed"] = spd_r.value.magnitude
                except Exception:
                    pass

            # Coolant
            if cool_r and (not cool_r.is_null()):
                try:
                    last_vals["coolant"] = cool_r.value.magnitude
                except Exception:
                    pass

            # Oil (may be unsupported)
            if oil_r and (not oil_r.is_null()):
                try:
                    last_vals["oil"] = oil_r.value.magnitude
                except Exception:
                    pass

            # Write back to state
            state["last_values"] = last_vals
            state["connected"] = obd_conn is not None and obd_conn.is_connected()
            state["last_update"] = datetime.utcnow().isoformat() + "Z"

        # Prepare CSV row
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        row = [ts,
               last_vals.get("rpm", 0),
               last_vals.get("speed", 0),
               last_vals.get("coolant", 0),
               last_vals.get("oil", "N/A")]

        buffer_rows.append(row)
        rows_since_flush += 1

        # Increment counters in state
        with state_lock:
            state["total_records"] += 1

        # Flush buffer occasionally
        if rows_since_flush >= CSV_BUFFER_FLUSH:
            try:
                with open(state["csv_path"], "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerows(buffer_rows)
                buffer_rows = []
                rows_since_flush = 0
            except Exception as e:
                print("CSV write error:", e)

        # Sleep remainder of interval
        elapsed = time.time() - start
        to_sleep = LOG_INTERVAL - elapsed
        if to_sleep > 0:
            _stop_event.wait(to_sleep)

    # Final flush
    if buffer_rows:
        try:
            with open(state["csv_path"], "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(buffer_rows)
        except Exception as e:
            print("Final CSV flush error:", e)

# ===== API ENDPOINTS =====

@app.route("/api/status", methods=["GET"])
def api_status():
    with state_lock:
        s = {
            "connected": state["connected"],
            "is_logging": state["is_logging"],
            "log_start_time": state["log_start_time"],
            "last_update": state["last_update"],
            "rpm": state["last_values"].get("rpm", 0),
            "speed": state["last_values"].get("speed", 0),
            "coolant": state["last_values"].get("coolant", 0),
            "oil": state["last_values"].get("oil", "N/A"),
            "total_records": state["total_records"]
        }
    return jsonify(s), 200

@app.route("/api/stats", methods=["GET"])
def api_stats():
    with state_lock:
        s = {
            "total_records": state["total_records"],
            "last_update": state["last_update"],
            "log_start_time": state["log_start_time"]
        }
    return jsonify(s), 200

@app.route("/api/start_logging", methods=["POST"])
def api_start_logging():
    global _log_thread, _stop_event
    with state_lock:
        if state["is_logging"]:
            return jsonify({"error": "Already logging"}), 400

        # Create CSV
        fname, path = new_csv_file()
        state["csv_file"] = fname
        state["csv_path"] = path
        state["log_start_time"] = datetime.utcnow().isoformat() + "Z"
        state["total_records"] = 0
        state["is_logging"] = True

    # Reset stop event & start thread
    _stop_event.clear()
    _log_thread = threading.Thread(target=logger_thread, daemon=True)
    _log_thread.start()

    return jsonify({"started": True, "start_time": state["log_start_time"], "filename": state["csv_file"]}), 200

@app.route("/api/stop_logging", methods=["POST"])
def api_stop_logging():
    global _stop_event, _log_thread
    with state_lock:
        if not state["is_logging"]:
            return jsonify({"error": "Not currently logging"}), 400
        state["is_logging"] = False

    # signal thread to stop
    _stop_event.set()
    if _log_thread:
        _log_thread.join(timeout=5)

    with state_lock:
        total = state["total_records"]
        fname = state["csv_file"]
        path = state["csv_path"]

    return jsonify({"stopped": True, "total_records": total, "filename": fname}), 200

@app.route("/api/clear_data", methods=["POST"])
def api_clear_data():
    global _stop_event, _log_thread
    # If logging, stop first
    with state_lock:
        was_logging = state["is_logging"]
    if was_logging:
        _stop_event.set()
        if _log_thread:
            _log_thread.join(timeout=5)
        with state_lock:
            state["is_logging"] = False

    # Delete CSV file if present
    with state_lock:
        path = state.get("csv_path")
        state["csv_file"] = None
        state["csv_path"] = None
        state["total_records"] = 0
        state["log_start_time"] = None
        state["last_update"] = None

    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print("Error removing CSV:", e)

    # Reset stop event for future runs
    _stop_event.clear()
    return jsonify({"cleared": True}), 200

@app.route("/api/download/csv", methods=["GET"])
def api_download_csv():
    with state_lock:
        path = state.get("csv_path")
        fname = state.get("csv_file")
    if not path or not os.path.exists(path):
        return jsonify({"error": "No CSV available"}), 404
    # send as attachment
    try:
        return send_file(path,
                         as_attachment=True,
                         download_name=fname,
                         mimetype="text/csv")
    except TypeError:
        # for older Flask versions that don't support download_name
        return send_file(path, as_attachment=True, attachment_filename=fname, mimetype="text/csv")

# ===== Run app =====
if __name__ == "__main__":
    # By default serve on 0.0.0.0 so your frontend can reach it; change port if needed
    app.run(host="0.0.0.0", port=5000, debug=False)

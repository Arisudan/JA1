import customtkinter as ctk
import obd
import time
import threading
import csv
import subprocess
import os
from datetime import datetime
import sys

# --- CONFIGURATION (Based on your input) ---
WIFI_SSID = "WIFI_OBDII"    # CHANGE THIS to your dongle's name
WIFI_PASS = "12345678"      # CHANGE THIS to your dongle's password
OBD_PORT = "socket://192.168.0.10:35000"
OBD_TIMEOUT = 5
CSV_DIR = "./logs"
LOG_INTERVAL = 0.1         # Polling frequency (10Hz)

# Global data cache and lock (Implementing Flask's state and state_lock)
_data_lock = threading.Lock()
_data_cache = {
    "connected": False,
    "is_logging": False,
    "last_values": {"rpm": 0, "speed": 0, "coolant": 0, "oil": "N/A"},
    "csv_path": None,
    "total_records": 0
}

# --- THREAD FOR CONTINUOUS DATA COLLECTION (Implementing Flask's logger_thread) ---
def obd_data_collector():
    """
    Background thread that continuously polls the OBD adapter at 10Hz
    and updates the global data cache. This runs regardless of logging status.
    """
    global obd_conn
    obd_conn = None # Initialize local connection object
    
    # Commands
    cmd_rpm = obd.commands.RPM
    cmd_speed = obd.commands.SPEED
    cmd_cool = obd.commands.COOLANT_TEMP
    cmd_oil = obd.commands.OIL_TEMP
    
    # Create CSV directory
    if not os.path.exists(CSV_DIR):
        os.makedirs(CSV_DIR)

    while True:
        start_time = time.time()
        
        # 1. Connection Check/Re-connect attempt
        if obd_conn is None or not obd_conn.is_connected():
            try:
                obd_conn = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=OBD_TIMEOUT, protocol="6")
                with _data_lock:
                    _data_cache["connected"] = obd_conn.is_connected()
            except Exception:
                with _data_lock:
                    _data_cache["connected"] = False
                time.sleep(1) # Wait longer on connection failure
                continue

        # 2. Query Data
        last_vals = _data_cache["last_values"].copy() # Get current values as default
        current_time = datetime.now()
        
        try:
            # Query all values forcefully
            rpm_r = obd_conn.query(cmd_rpm, force=True)
            spd_r = obd_conn.query(cmd_speed, force=True)
            cool_r = obd_conn.query(cmd_cool, force=True)
            oil_r = obd_conn.query(cmd_oil, force=True)

            # 3. Update Last-Good Values (Caching)
            if rpm_r and (not rpm_r.is_null()):
                last_vals["rpm"] = int(rpm_r.value.magnitude)
            if spd_r and (not spd_r.is_null()):
                last_vals["speed"] = int(spd_r.value.magnitude)
            if cool_r and (not cool_r.is_null()):
                last_vals["coolant"] = int(cool_r.value.magnitude)
            if oil_r and (not oil_r.is_null()):
                last_vals["oil"] = int(oil_r.value.magnitude)
        
        except Exception:
            # Communication error occurred, connection is likely down.
            with _data_lock:
                _data_cache["connected"] = False
            obd_conn = None # Force re-initialization next loop
            last_vals = {"rpm": 0, "speed": 0, "coolant": 0, "oil": "N/A"} # Clear display values on error

        # 4. Write back to global cache
        with _data_lock:
            _data_cache["last_values"] = last_vals
            _data_cache["connected"] = obd_conn is not None and obd_conn.is_connected()
        
        # 5. Handle Logging (Writing to CSV)
        if _data_cache["is_logging"] and _data_cache["connected"]:
            try:
                with open(_data_cache["csv_path"], "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        current_time.strftime('%Y-%m-%d %H:%M:%S.%f'),
                        last_vals["rpm"], last_vals["speed"],
                        last_vals["coolant"], last_vals["oil"]
                    ])
                    # Update record count (no need for complex buffer/flush logic in this version)
                    with _data_lock:
                        _data_cache["total_records"] += 1
            except Exception as e:
                print("CSV write error:", e)

        # 6. Control Thread Sleep Time
        elapsed = time.time() - start_time
        to_sleep = LOG_INTERVAL - elapsed
        if to_sleep > 0:
            time.sleep(to_sleep)


# --- THE DASHBOARD APP ---
class CarDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window Setup
        self.title("OBDII Dashboard")
        self.geometry("800x480")
        self.attributes('-fullscreen', True) 
        ctk.set_appearance_mode("Dark")
        
        # Start the background data collector thread immediately
        threading.Thread(target=obd_data_collector, daemon=True).start()
        
        # Initialize the GUI update loop
        self._gui_update_loop()

        # Layout: Grid System
        self.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.grid_rowconfigure(0, weight=3) # Gauges area
        self.grid_rowconfigure(1, weight=1) # Buttons area

        # --- GAUGES (BIG LABELS) ---
        self.lbl_speed = self.create_gauge("SPEED", "0", "km/h", 0, 0)
        self.lbl_rpm = self.create_gauge("RPM", "0", "rpm", 0, 1)
        self.lbl_coolant = self.create_gauge("COOLANT", "0", "°C", 0, 2)
        self.lbl_oil = self.create_gauge("OIL", "N/A", "°C", 0, 3)

        # --- BUTTONS ---
        # 1. WiFi Connect (Kept for initial connection trigger)
        self.btn_wifi = ctk.CTkButton(self, text="CONNECT WIFI", command=self.connect_wifi, 
                                      fg_color="#0066CC", height=60, font=("Arial", 18, "bold"))
        self.btn_wifi.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        # 2. Start/Stop Button
        self.btn_start = ctk.CTkButton(self, text="START LOG", command=self.toggle_logging, 
                                       fg_color="#009933", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_start.grid(row=1, column=1, padx=10, pady=10, sticky="ew")

        # 3. Stop (Cleared, combined into start/stop)
        self.btn_export = ctk.CTkButton(self, text="EXPORT LAST LOG", command=self.export_log, 
                                      fg_color="#555555", height=60, font=("Arial", 18, "bold"))
        self.btn_export.grid(row=1, column=2, padx=10, pady=10, sticky="ew")


        # 4. Exit/Export Button
        self.btn_exit = ctk.CTkButton(self, text="EXIT", command=self.close_app, 
                                      fg_color="#CC0000", height=60, font=("Arial", 18, "bold"))
        self.btn_exit.grid(row=1, column=3, padx=10, pady=10, sticky="ew")

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Status: Starting Data Collector...", text_color="gray")
        self.lbl_status.grid(row=2, column=0, columnspan=4, pady=5)
        
    def create_gauge(self, title, value, unit, r, c):
        frame = ctk.CTkFrame(self)
        frame.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
        
        ctk.CTkLabel(frame, text=title, font=("Arial", 16)).pack(pady=(20, 0))
        lbl_value = ctk.CTkLabel(frame, text=value, font=("Arial", 50, "bold"), text_color="#33CCFF")
        lbl_value.pack(expand=True)
        ctk.CTkLabel(frame, text=unit, font=("Arial", 14)).pack(pady=(0, 20))
        return lbl_value

    # --- LOGIC ---
    def _gui_update_loop(self):
        """Fetches data from the global cache and updates the GUI."""
        with _data_lock:
            connected = _data_cache["connected"]
            is_logging = _data_cache["is_logging"]
            values = _data_cache["last_values"]
            total_records = _data_cache["total_records"]

        # 1. Update Gauges
        self.lbl_rpm.configure(text=str(values.get("rpm", 0)))
        self.lbl_speed.configure(text=str(values.get("speed", 0)))
        self.lbl_coolant.configure(text=str(values.get("coolant", 0)))
        self.lbl_oil.configure(text=str(values.get("oil", "N/A")))
        
        # 2. Update Status Bar
        if connected:
            self.btn_start.configure(state="normal")
            if is_logging:
                self.lbl_status.configure(text=f"LOGGING | Records: {total_records} | File: {_data_cache['csv_path'].split('/')[-1]}", text_color="orange")
            else:
                self.lbl_status.configure(text="ECU Connected! Ready to Log.", text_color="#00FF00")
        else:
            self.btn_start.configure(state="disabled")
            self.lbl_status.configure(text="Status: DISCONNECTED / Attempting Reconnect...", text_color="red")
            
        # Schedule next update
        self.after(250, self._gui_update_loop) # Update 4 times per second (250ms)

    def connect_wifi(self):
        """Runs nmcli command to connect to the dongle (Remains a separate thread due to subprocess)."""
        threading.Thread(target=self._wifi_connect_worker, daemon=True).start()

    def _wifi_connect_worker(self):
        self.after(0, self.lbl_status.configure, {"text": "Connecting to WiFi...", "text_color": "yellow"})
        
        cmd = f'nmcli dev wifi connect "{WIFI_SSID}" password "{WIFI_PASS}"'
        try:
            # Run terminal command to connect
            subprocess.run(cmd, shell=True, check=True)
            self.after(0, self.lbl_status.configure, {"text": f"WiFi Connected to {WIFI_SSID}. Waiting for OBD data...", "text_color": "green"})
            # The background thread (obd_data_collector) will automatically handle the OBD connection from here.
        except subprocess.CalledProcessError:
            self.after(0, self.lbl_status.configure, {"text": "WiFi Connection Failed (Check nmcli/credentials).", "text_color": "red"})

    def toggle_logging(self):
        """Starts or stops logging based on global state."""
        with _data_lock:
            was_logging = _data_cache["is_logging"]

        if was_logging:
            # Stop Logging
            with _data_lock:
                _data_cache["is_logging"] = False
            self.btn_start.configure(text="START LOG", fg_color="#009933")
            # Log file is already written by the collector thread, just need to confirm stop
            self.lbl_status.configure(text=f"Logging Stopped. {_data_cache['total_records']} Records.", text_color="white")
        else:
            # Start Logging
            with _data_lock:
                # Setup new CSV file for the background thread
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"obd_log_{ts}.csv"
                path = os.path.join(CSV_DIR, fname)
                
                # Create CSV file with header
                with open(path, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["timestamp", "rpm", "speed_kmh", "coolant_c", "oil_c"])

                # Update state for the background thread
                _data_cache["csv_path"] = path
                _data_cache["total_records"] = 0
                _data_cache["is_logging"] = True
                
            self.btn_start.configure(text="STOP LOG", fg_color="#CC0000")
            self.lbl_status.configure(text="LOGGING ACTIVE...", text_color="orange")

    def export_log(self):
        """Displays the path of the last log file."""
        with _data_lock:
            path = _data_cache["csv_path"]
            records = _data_cache["total_records"]
            
        if not path or not os.path.exists(path):
            ctk.CTkMessagebox.showerror("Export Error", "No log file found.")
        else:
            ctk.CTkMessagebox.showinfo("Log File Saved", f"File saved:\n{path}\nRecords: {records}")

    def close_app(self):
        """Stops application gracefully."""
        # Clean up logging state before exit
        with _data_lock:
            _data_cache["is_logging"] = False
        
        # Display exit message and destroy
        ctk.CTkMessagebox.showinfo("Exiting", "Dashboard closing. Any active logging has stopped.")
        self.destroy()
        sys.exit() # Ensure all background threads (data collector) terminate

# --- RUN IT ---
if __name__ == "__main__":
    app = CarDashboard()
    app.mainloop()

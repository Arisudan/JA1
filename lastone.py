import customtkinter as ctk
import obd
import time
import threading
import csv
import subprocess
import os
from datetime import datetime
import sys

# --- CONFIGURATION (Match your adapter) ---
# IMPORTANT: If you encounter WiFi issues, check if you need to install 'network-manager'
# and ensure 'nmcli' is available on your Raspberry Pi OS.
WIFI_SSID = "WiFi_OBDII"  # CHANGE THIS to your dongle's network name
WIFI_PASS = "12345678"    # CHANGE THIS to your dongle's password
OBD_PORT = "socket://192.168.0.10:35000"
LOG_INTERVAL = 0.1          # Polling frequency (10 Hz)
CSV_BUFFER_FLUSH = 10       # Flush CSV to disk every N rows
CSV_DIR = "./logs"
if not os.path.exists(CSV_DIR):
    os.makedirs(CSV_DIR)

# --- GLOBAL SHARED STATE (FLASK Backend Logic) ---
# This dictionary holds the current values and logging status, protected by a lock.
STATE_LOCK = threading.Lock()
STATE = {
    "connected": False,
    "is_logging": False,
    "log_start_time": None,
    "last_update": None,
    "total_records": 0,
    "last_values": {
        "rpm": 0,
        "speed": 0,
        "coolant": 0,
        "oil": "N/A"
    },
    "csv_path": None
}

# --- OBD CONNECTION ---
obd_conn = None

def init_obd():
    """Initializes the OBD connection object."""
    global obd_conn
    try:
        # We use a timeout to prevent blocking indefinitely
        obd_conn = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=5, protocol="6")
        with STATE_LOCK:
            STATE["connected"] = obd_conn.is_connected()
    except Exception:
        with STATE_LOCK:
            STATE["connected"] = False

def new_csv_file():
    """Creates a new CSV file and returns its path/filename."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"obd_log_{ts}.csv"
    path = os.path.join(CSV_DIR, fname)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "rpm", "speed_kmh", "coolant_c", "oil_c"])
    return fname, path


# --- HIGH-FREQUENCY DATA POLLING THREAD ---
def data_polling_thread():
    """Continuously polls OBD for data, updates global state, and logs if enabled."""
    global obd_conn
    buffer_rows = []
    rows_since_flush = 0
    
    # Define commands once
    cmd_rpm = obd.commands.RPM
    cmd_speed = obd.commands.SPEED
    cmd_cool = obd.commands.COOLANT_TEMP
    cmd_oil = obd.commands.OIL_TEMP

    while True:
        start = time.time()

        with STATE_LOCK:
            current_connection_state = STATE["connected"]
            is_logging = STATE["is_logging"]
            last_vals = STATE["last_values"].copy()
            csv_path = STATE.get("csv_path")
            total_records = STATE["total_records"]
        
        # 1. Connection Check and Re-connect Attempt
        if not current_connection_state or obd_conn is None:
            init_obd()
            time.sleep(1) # Wait longer if disconnected

        # 2. Query OBD (Always runs to keep gauges updated)
        try:
            if obd_conn and obd_conn.is_connected():
                # Query with force=True to minimize adapter caching
                rpm_r = obd_conn.query(cmd_rpm, force=True)
                spd_r = obd_conn.query(cmd_speed, force=True)
                cool_r = obd_conn.query(cmd_cool, force=True)
                oil_r = obd_conn.query(cmd_oil, force=True)
                
                # --- LAST-GOOD VALUE LOGIC (Fixes the flicker to zero) ---
                # Update last_vals with current magnitude, preserving last value if query failed
                if rpm_r and (not rpm_r.is_null()):
                    last_vals["rpm"] = int(rpm_r.value.magnitude)
                if spd_r and (not spd_r.is_null()):
                    last_vals["speed"] = int(spd_r.value.magnitude)
                if cool_r and (not cool_r.is_null()):
                    last_vals["coolant"] = int(cool_r.value.magnitude)
                if oil_r and (not oil_r.is_null()):
                    last_vals["oil"] = int(oil_r.value.magnitude)
                # Note: If a query fails, the old last_vals are used, preventing the flicker.

            with STATE_LOCK:
                STATE["connected"] = obd_conn is not None and obd_conn.is_connected()
                STATE["last_values"] = last_vals
                STATE["last_update"] = datetime.now().isoformat()
            
        except Exception:
            # If any communication error occurs (e.g., disconnection), mark as disconnected
            with STATE_LOCK:
                STATE["connected"] = False
        
        # 3. CSV Logging (Only if logging flag is True)
        if is_logging and STATE["connected"]:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            row = [ts,
                   last_vals.get("rpm", 0),
                   last_vals.get("speed", 0),
                   last_vals.get("coolant", 0),
                   last_vals.get("oil", "N/A")]
            
            buffer_rows.append(row)
            rows_since_flush += 1

            with STATE_LOCK:
                STATE["total_records"] += 1

            # Flush buffer occasionally
            if rows_since_flush >= CSV_BUFFER_FLUSH:
                try:
                    if csv_path:
                        with open(csv_path, "a", newline="") as f:
                            writer = csv.writer(f)
                            writer.writerows(buffer_rows)
                        buffer_rows = []
                        rows_since_flush = 0
                except Exception as e:
                    # Log error but keep trying to log
                    print("CSV write error:", e)

        # 4. Sleep remainder of interval
        elapsed = time.time() - start
        to_sleep = LOG_INTERVAL - elapsed
        if to_sleep > 0:
            time.sleep(to_sleep)

    # 5. Final flush before thread exits (if stop logging is called)
    if buffer_rows and csv_path:
        try:
            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(buffer_rows)
        except Exception as e:
            print("Final CSV flush error:", e)


# --- CUSTOMTKINTER GUI CLASS ---
class CarDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("OBDII Dashboard")
        self.geometry("800x480")
        self.attributes('-fullscreen', True) # Remove for windowed mode
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        # Protocol handler for clean exit
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        # Layout: Grid System
        self.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.grid_rowconfigure(0, weight=3) # Gauges area
        self.grid_rowconfigure(1, weight=1) # Buttons area
        self.grid_rowconfigure(2, weight=0) # Status area

        # --- GAUGES (BIG LABELS) ---
        self.lbl_speed = self.create_gauge("SPEED", "----", "km/h", 0, 0)
        self.lbl_rpm = self.create_gauge("RPM", "----", "rpm", 0, 1)
        self.lbl_coolant = self.create_gauge("COOLANT", "----", "°C", 0, 2)
        self.lbl_oil = self.create_gauge("OIL", "----", "°C", 0, 3)

        # --- BUTTONS ---
        self.btn_wifi = ctk.CTkButton(self, text="CONNECT WIFI", command=self.connect_wifi, 
                                      fg_color="#0066CC", height=60, font=("Arial", 18, "bold"))
        self.btn_wifi.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        self.btn_start = ctk.CTkButton(self, text="START LOG", command=self.start_logging, 
                                       fg_color="#009933", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_start.grid(row=1, column=1, padx=10, pady=10, sticky="ew")

        self.btn_stop = ctk.CTkButton(self, text="STOP LOG", command=self.stop_logging, 
                                      fg_color="#CC0000", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_stop.grid(row=1, column=2, padx=10, pady=10, sticky="ew")

        self.btn_exit = ctk.CTkButton(self, text="EXIT & SAVE", command=self.close_app, 
                                      fg_color="#555555", height=60, font=("Arial", 18, "bold"))
        self.btn_exit.grid(row=1, column=3, padx=10, pady=10, sticky="ew")

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Status: Initializing...", text_color="gray", font=("Arial", 14))
        self.lbl_status.grid(row=2, column=0, columnspan=2, pady=5, sticky="w", padx=10)
        
        self.lbl_records = ctk.CTkLabel(self, text="Records: 0", text_color="gray", font=("Arial", 14))
        self.lbl_records.grid(row=2, column=2, columnspan=2, pady=5, sticky="e", padx=10)

        # Start the GUI update loop
        self.gui_update_loop()


    def create_gauge(self, title, value, unit, r, c):
        frame = ctk.CTkFrame(self)
        frame.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
        
        ctk.CTkLabel(frame, text=title, font=("Arial", 16)).pack(pady=(20, 0))
        lbl_value = ctk.CTkLabel(frame, text=value, font=("Arial", 50, "bold"), text_color="#33CCFF")
        lbl_value.pack(expand=True)
        ctk.CTkLabel(frame, text=unit, font=("Arial", 14)).pack(pady=(0, 20))
        return lbl_value


    # --- GUI UPDATE LOOP (Reads Global State Safely) ---
    def gui_update_loop(self):
        """Reads data from the global STATE and updates the GUI safely."""
        try:
            with STATE_LOCK:
                connected = STATE["connected"]
                is_logging = STATE["is_logging"]
                last_vals = STATE["last_values"]
                records = STATE["total_records"]
            
            # Update Gauges (Reading from last-good values)
            self.lbl_rpm.configure(text=str(last_vals['rpm']))
            self.lbl_speed.configure(text=str(last_vals['speed']))
            self.lbl_coolant.configure(text=str(last_vals['coolant']))
            self.lbl_oil.configure(text=str(last_vals['oil']))
            self.lbl_records.configure(text=f"Records: {records}")

            # Update Status and Button States
            if not connected:
                self.lbl_status.configure(text="Status: Disconnected (Check Wi-Fi/OBD)", text_color="red")
                self.btn_start.configure(state="disabled")
                self.btn_stop.configure(state="disabled")
                self.btn_wifi.configure(state="normal")
            else:
                if is_logging:
                    self.lbl_status.configure(text=f"Status: LOGGING ACTIVE ({LOG_INTERVAL*1000}ms)", text_color="orange")
                    self.btn_start.configure(state="disabled")
                    self.btn_stop.configure(state="normal")
                else:
                    self.lbl_status.configure(text="Status: ECU Connected (Polling Data)", text_color="#00FF00")
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                
                self.btn_wifi.configure(state="disabled")

        except Exception as e:
            # print(f"GUI Update Error: {e}")
            pass # Keep silent to maintain smooth animation

        # Schedule the next update (50ms for smooth 20 FPS GUI refresh)
        self.after(50, self.gui_update_loop)


    # --- LOGIC FUNCTIONS ---
    def connect_wifi(self):
        """Runs nmcli command and then attempts OBD connect."""
        self.lbl_status.configure(text="Connecting to WiFi...", text_color="yellow")
        self.update()
        
        # Wi-Fi Connection
        cmd = f'nmcli dev wifi connect "{WIFI_SSID}" password "{WIFI_PASS}"'
        try:
            # Run terminal command to connect silently
            subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.lbl_status.configure(text=f"WiFi Connected. Initializing OBD...", text_color="yellow")
            
            # OBD Connection (non-blocking, handled by the data_polling_thread)
            init_obd()

        except subprocess.CalledProcessError:
            self.lbl_status.configure(text="WiFi Connection Failed. Try Again.", text_color="red")
        except Exception as e:
            self.lbl_status.configure(text=f"Connection Error: {e}", text_color="red")


    def start_logging(self):
        """Starts the logging process by updating the global state."""
        with STATE_LOCK:
            if not STATE["connected"]:
                ctk.CTkMessagebox.show_warning("Warning", "Cannot start logging: ECU is not connected.")
                return
            
            # Create CSV file and update state
            fname, path = new_csv_file()
            STATE["csv_path"] = path
            STATE["log_start_time"] = datetime.now().isoformat()
            STATE["total_records"] = 0
            STATE["is_logging"] = True
            
        self.lbl_status.configure(text=f"LOGGING STARTED. Saving to {fname}", text_color="orange")


    def stop_logging(self):
        """Stops the logging process."""
        with STATE_LOCK:
            STATE["is_logging"] = False
        
        ctk.CTkMessagebox.showinfo("Logging Stopped", f"Logging stopped. Data saved to file: {os.path.basename(STATE.get('csv_path', ''))}")
        self.lbl_status.configure(text="Logging Stopped. Data saved.", text_color="white")


    def close_app(self):
        """Stops logging, attempts final CSV flush, and closes the application gracefully."""
        # Stop logging if active
        with STATE_LOCK:
            if STATE["is_logging"]:
                STATE["is_logging"] = False
                # The data_polling_thread handles the final flush implicitly

        # Close OBD connection
        if obd_conn:
            obd_conn.close()
            
        ctk.CTkMessagebox.showinfo("Exiting", "Dashboard closed. Goodbye.")
        self.quit()
        sys.exit()

# --- START THE DATA THREAD AND RUN THE APP ---
if __name__ == "__main__":
    # Start the continuous data polling thread immediately
    threading.Thread(target=data_polling_thread, daemon=True).start()
    
    try:
        app = CarDashboard()
        app.mainloop()
    except Exception as e:
        print(f"FATAL APPLICATION ERROR: {e}")

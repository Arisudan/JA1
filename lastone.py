import customtkinter as ctk
import obd
import time
import threading
import csv
import subprocess
import os
from datetime import datetime

# --- CONFIGURATION ---
WIFI_SSID = "WIFI_OBDII"
OBD_PORT = "socket://192.168.0.10:35000"
LOG_INTERVAL = 0.1       # seconds (10 Hz)
FLUSH_EVERY = 10         # flush CSV buffer every N rows
CSV_DIR = "./"
RECONNECT_INTERVAL = 2   # seconds between connection retry attempts

# --- THE DASHBOARD APP ---
class CarDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("OBDII Dashboard")
        self.geometry("800x480")
        # self.attributes('-fullscreen', True)  # optional
        ctk.set_appearance_mode("Dark")
        
        # State
        self.running = False
        self.connection = None
        self.csv_file = None
        self.writer = None
        self.csv_buffer = []
        self.buffer_count = 0
        self._thread = None
        self._stop_event = threading.Event()

        # last-good cache (prevents zeros)
        self.last = {
            "rpm": 0,
            "speed": 0,
            "coolant": 0,
            "oil": "N/A"
        }

        # Layout
        self.grid_columnconfigure((0,1,2,3), weight=1)
        self.grid_rowconfigure(0, weight=3)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self.lbl_speed = self.create_gauge("SPEED", "0", "km/h", 0, 0)
        self.lbl_rpm = self.create_gauge("RPM", "0", "rpm", 0, 1)
        self.lbl_coolant = self.create_gauge("COOLANT", "0", "°C", 0, 2)
        self.lbl_oil = self.create_gauge("OIL", "N/A", "°C", 0, 3)

        self.btn_wifi = ctk.CTkButton(self, text="CONNECT WIFI", command=self.connect_wifi,
                                      fg_color="#0066CC", height=60, font=("Arial", 18, "bold"))
        self.btn_wifi.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        self.btn_start = ctk.CTkButton(self, text="START LOG", command=self.start_logging,
                                       fg_color="#009933", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_start.grid(row=1, column=1, padx=10, pady=10, sticky="ew")

        self.btn_stop = ctk.CTkButton(self, text="STOP", command=self.stop_logging,
                                      fg_color="#CC0000", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_stop.grid(row=1, column=2, padx=10, pady=10, sticky="ew")

        self.btn_exit = ctk.CTkButton(self, text="EXIT & SAVE", command=self.close_app,
                                      fg_color="#555555", height=60, font=("Arial", 18, "bold"))
        self.btn_exit.grid(row=1, column=3, padx=10, pady=10, sticky="ew")

        self.lbl_status = ctk.CTkLabel(self, text="Status: Disconnected", text_color="gray")
        self.lbl_status.grid(row=2, column=0, columnspan=4, pady=5)

        # try auto connect to OBD if available
        self.after(500, self.try_init_obd)

    def create_gauge(self, title, value, unit, r, c):
        frame = ctk.CTkFrame(self)
        frame.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
        ctk.CTkLabel(frame, text=title, font=("Arial", 16)).pack(pady=(20, 0))
        lbl_value = ctk.CTkLabel(frame, text=value, font=("Arial", 50, "bold"), text_color="#33CCFF")
        lbl_value.pack(expand=True)
        ctk.CTkLabel(frame, text=unit, font=("Arial", 14)).pack(pady=(0, 20))
        return lbl_value

    # ----- Connectivity -----
    def connect_wifi(self):
        self.lbl_status.configure(text="Connecting to WiFi...", text_color="yellow")
        self.update()
        cmd = f'nmcli dev wifi connect "{WIFI_SSID}"'
        try:
            subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.lbl_status.configure(text=f"WiFi Connected to {WIFI_SSID}", text_color="green")
            self.after(200, self.try_init_obd)
        except subprocess.CalledProcessError:
            self.lbl_status.configure(text="WiFi Connection Failed", text_color="red")

    def try_init_obd(self):
        """Attempt to open OBD connection; non-blocking attempts every RECONNECT_INTERVAL if fails."""
        try:
            if not self.connection or not (hasattr(self.connection, 'is_connected') and self.connection.is_connected()):
                # try create connection
                try:
                    self.connection = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=5, protocol="6")
                except Exception as e:
                    self.connection = None
                if self.connection and self.connection.is_connected():
                    self.lbl_status.configure(text="ECU Connected! Ready to Log.", text_color="#00FF00")
                    self.btn_start.configure(state="normal")
                    self.btn_wifi.configure(state="disabled")
                else:
                    self.lbl_status.configure(text="ECU Not Connected", text_color="red")
                    # schedule retry
                    self.after(int(RECONNECT_INTERVAL*1000), self.try_init_obd)
        except Exception as e:
            self.lbl_status.configure(text=f"OBD init error", text_color="red")

    # ----- Logging control -----
    def start_logging(self):
        if not self.connection or not self.connection.is_connected():
            self.lbl_status.configure(text="Not connected to ECU", text_color="red")
            return

        filename = f"Trip_Log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = os.path.join(CSV_DIR, filename)
        self.csv_file = open(path, mode='w', newline='')
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(["Timestamp", "RPM", "Speed", "Coolant", "Oil"])
        self.csv_buffer = []
        self.buffer_count = 0

        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.logging_loop, daemon=True)
        self._thread.start()

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.lbl_status.configure(text=f"LOGGING TO: {filename}", text_color="#00FF00")

    def stop_logging(self):
        # Stop thread
        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

        # flush remaining buffer & close file
        if self.csv_buffer and self.writer and self.csv_file:
            try:
                self.writer.writerows(self.csv_buffer)
            except Exception:
                pass
            self.csv_buffer = []
            self.buffer_count = 0

        if self.csv_file:
            try:
                self.csv_file.close()
            except Exception:
                pass
            self.csv_file = None
            self.writer = None

        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.lbl_status.configure(text="Logging Stopped. File Saved.", text_color="white")

    def close_app(self):
        self.stop_logging()
        # try to gracefully close OBD
        try:
            if self.connection and hasattr(self.connection, "close"):
                self.connection.close()
        except Exception:
            pass
        self.destroy()

    # ----- Worker loop -----
    def logging_loop(self):
        cmd_rpm = obd.commands.RPM
        cmd_speed = obd.commands.SPEED
        cmd_cool = obd.commands.COOLANT_TEMP
        cmd_oil = obd.commands.OIL_TEMP

        while not self._stop_event.is_set():
            start = time.time()

            # ensure connection
            if not (self.connection and self.connection.is_connected()):
                # attempt reconnect (non-blocking short sleep)
                try:
                    self.connection = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=5, protocol="6")
                except Exception:
                    self.connection = None

            # Query safely
            rpm_r = speed_r = cool_r = oil_r = None
            try:
                if self.connection and self.connection.is_connected():
                    rpm_r = self.connection.query(cmd_rpm, force=True)
                    speed_r = self.connection.query(cmd_speed, force=True)
                    cool_r = self.connection.query(cmd_cool, force=True)
                    oil_r = self.connection.query(cmd_oil, force=True)
            except Exception:
                # communication hiccup, continue with last values
                pass

            # Update last-good values only when valid
            if rpm_r and not rpm_r.is_null():
                try:
                    self.last["rpm"] = int(rpm_r.value.magnitude)
                except Exception:
                    pass
            if speed_r and not speed_r.is_null():
                try:
                    self.last["speed"] = int(speed_r.value.magnitude)
                except Exception:
                    pass
            if cool_r and not cool_r.is_null():
                try:
                    self.last["coolant"] = int(cool_r.value.magnitude)
                except Exception:
                    pass
            if oil_r and not oil_r.is_null():
                try:
                    self.last["oil"] = int(oil_r.value.magnitude)
                except Exception:
                    pass

            # Prepare CSV row using last-good values (no zeros unless truly initial)
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            row = [ts, self.last["rpm"], self.last["speed"], self.last["coolant"], self.last["oil"]]

            # Buffer row
            if self.writer is not None:
                self.csv_buffer.append(row)
                self.buffer_count += 1

            # Periodically flush buffer to disk (less IO)
            if self.buffer_count >= FLUSH_EVERY and self.writer and self.csv_file:
                try:
                    self.writer.writerows(self.csv_buffer)
                    # Optionally do a single flush to reduce OS caching issues:
                    self.csv_file.flush()
                except Exception:
                    pass
                self.csv_buffer = []
                self.buffer_count = 0

            # Update GUI (single update with last-good values)
            try:
                self.after(0, self.update_labels,
                           self.last["rpm"], self.last["speed"], self.last["coolant"], self.last["oil"])
            except Exception:
                pass

            # Sleep to maintain loop rate
            elapsed = time.time() - start
            to_sleep = LOG_INTERVAL - elapsed
            if to_sleep > 0:
                self._stop_event.wait(to_sleep)

        # Final flush when loop ends
        if self.csv_buffer and self.writer and self.csv_file:
            try:
                self.writer.writerows(self.csv_buffer)
                self.csv_file.flush()
            except Exception:
                pass
            self.csv_buffer = []
            self.buffer_count = 0

    def update_labels(self, rpm, speed, cool, oil):
        self.lbl_rpm.configure(text=str(rpm))
        self.lbl_speed.configure(text=str(speed))
        self.lbl_coolant.configure(text=str(cool))
        self.lbl_oil.configure(text=str(oil))

# --- RUN ---
if __name__ == "__main__":
    app = CarDashboard()
    app.mainloop()

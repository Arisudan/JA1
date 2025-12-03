import customtkinter as ctk
import obd
import time
import threading
import csv
import subprocess
import os
from datetime import datetime

# --- CONFIGURATION ---
WIFI_SSID = "WiFi_OBDII"  # CHANGE THIS to your exact dongle name (Case Sensitive!)
OBD_PORT = "socket://192.168.0.10:35000"

# --- THE DASHBOARD APP ---
class CarDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup (Fullscreen for 7-inch Touchscreen)
        self.title("OBDII Dashboard")
        self.geometry("800x480")
        self.attributes('-fullscreen', True) 
        ctk.set_appearance_mode("Dark")
        
        # System Variables
        self.running = False
        self.connection = None
        self.csv_file = None
        self.writer = None

        # STORAGE FOR "LAST KNOWN VALUE" (Prevents flashing zeros)
        self.val_rpm = 0
        self.val_speed = 0
        self.val_cool = 0
        self.val_oil = "N/A"
        
        # Layout: Grid System (2 Rows, 4 Columns)
        self.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.grid_rowconfigure(0, weight=3) # Gauges area
        self.grid_rowconfigure(1, weight=1) # Buttons area

        # --- GAUGES (BIG LABELS) ---
        self.lbl_speed = self.create_gauge("SPEED", "0", "km/h", 0, 0)
        self.lbl_rpm = self.create_gauge("RPM", "0", "rpm", 0, 1)
        self.lbl_coolant = self.create_gauge("COOLANT", "0", "°C", 0, 2)
        self.lbl_oil = self.create_gauge("OIL", "N/A", "°C", 0, 3)

        # --- BUTTONS ---
        # 1. WiFi Connect Button
        self.btn_wifi = ctk.CTkButton(self, text="CONNECT WIFI", command=self.connect_wifi, 
                                      fg_color="#0066CC", height=60, font=("Arial", 18, "bold"))
        self.btn_wifi.grid(row=1, column=0, padx=10, pady=10, sticky="ew")

        # 2. Start Button
        self.btn_start = ctk.CTkButton(self, text="START LOG", command=self.start_logging, 
                                       fg_color="#009933", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_start.grid(row=1, column=1, padx=10, pady=10, sticky="ew")

        # 3. Stop Button
        self.btn_stop = ctk.CTkButton(self, text="STOP", command=self.stop_logging, 
                                      fg_color="#CC0000", height=60, font=("Arial", 18, "bold"), state="disabled")
        self.btn_stop.grid(row=1, column=2, padx=10, pady=10, sticky="ew")

        # 4. Exit/Export Button
        self.btn_exit = ctk.CTkButton(self, text="EXIT & SAVE", command=self.close_app, 
                                      fg_color="#555555", height=60, font=("Arial", 18, "bold"))
        self.btn_exit.grid(row=1, column=3, padx=10, pady=10, sticky="ew")

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Status: Disconnected", text_color="gray")
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
    def connect_wifi(self):
        """Runs nmcli command to connect to the dongle (OPEN NETWORK)"""
        self.lbl_status.configure(text="Connecting to WiFi...", text_color="yellow")
        self.update()
        
        # Run terminal command to connect (No Password)
        cmd = f'nmcli dev wifi connect "{WIFI_SSID}"'
        try:
            subprocess.run(cmd, shell=True, check=True)
            self.lbl_status.configure(text=f"WiFi Connected to {WIFI_SSID}", text_color="green")
            
            # Now try to connect to OBD port
            self.connect_obd()
        except subprocess.CalledProcessError:
            self.lbl_status.configure(text="WiFi Connection Failed", text_color="red")

    def connect_obd(self):
        """Connects to the Python-OBD library"""
        self.lbl_status.configure(text="Connecting to ECU...", text_color="yellow")
        self.update()
        
        try:
            # check_voltage=False prevents crash on clones
            # protocol="6" forces modern CAN bus
            self.connection = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=5, protocol="6")
            
            if self.connection.is_connected():
                self.lbl_status.configure(text="ECU Connected! Ready to Log.", text_color="#00FF00")
                self.btn_start.configure(state="normal")
                self.btn_wifi.configure(state="disabled") 
            else:
                self.lbl_status.configure(text="ECU Connection Failed", text_color="red")
        except Exception as e:
            self.lbl_status.configure(text=f"Error: {str(e)}", text_color="red")

    def start_logging(self):
        """Starts the threading loop"""
        if not self.connection or not self.connection.is_connected():
            return

        # Setup CSV
        filename = f"Trip_Log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.csv_file = open(filename, mode='w', newline='')
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(["Timestamp", "RPM", "Speed", "Coolant", "Oil"])

        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.lbl_status.configure(text=f"LOGGING TO: {filename}", text_color="#00FF00")

        # Start the worker thread
        threading.Thread(target=self.logging_loop, daemon=True).start()

    def stop_logging(self):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.lbl_status.configure(text="Logging Stopped. File Saved.", text_color="white")
        
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None

    def close_app(self):
        self.stop_logging()
        self.destroy()

    def logging_loop(self):
        """The Loop: Reads Data -> Updates Variables -> Updates Screen"""
        cmd_rpm = obd.commands.RPM
        cmd_speed = obd.commands.SPEED
        cmd_cool = obd.commands.COOLANT_TEMP
        cmd_oil = obd.commands.OIL_TEMP

        while self.running:
            # 1. Query Data (force=True ignores 'not supported' errors)
            rpm = self.connection.query(cmd_rpm, force=True)
            speed = self.connection.query(cmd_speed, force=True)
            cool = self.connection.query(cmd_cool, force=True)
            oil = self.connection.query(cmd_oil, force=True)

            # 2. FILTERING LOGIC (The Fix for Flashing Zeros)
            # Only update self.val_XXX if the new data is VALID.
            # If data is None, we just keep the old value.
            
            if not rpm.is_null():
                self.val_rpm = int(rpm.value.magnitude)
            
            if not speed.is_null():
                self.val_speed = int(speed.value.magnitude)
            
            if not cool.is_null():
                self.val_cool = int(cool.value.magnitude)

            if not oil.is_null():
                self.val_oil = int(oil.value.magnitude)

            # 3. Log to CSV (Write the stable values)
            if self.writer:
                timestamp = datetime.now().strftime('%H:%M:%S')
                self.writer.writerow([timestamp, self.val_rpm, self.val_speed, self.val_cool, self.val_oil])
                self.csv_file.flush()

            # 4. Update GUI (Must use .after)
            self.after(0, self.update_labels, self.val_rpm, self.val_speed, self.val_cool, self.val_oil)
            
            # Tiny sleep to prevent CPU overload
            time.sleep(0.1)

    def update_labels(self, rpm, speed, cool, oil):
        """Updates the text on screen"""
        self.lbl_rpm.configure(text=str(rpm))
        self.lbl_speed.configure(text=str(speed))
        self.lbl_coolant.configure(text=str(cool))
        self.lbl_oil.configure(text=str(oil))

# --- RUN IT ---
if __name__ == "__main__":
    app = CarDashboard()
    app.mainloop()

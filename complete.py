import customtkinter as ctk
import obd
import time
import threading
import csv
import subprocess
import os
from datetime import datetime

# --- CONFIGURATION ---
WIFI_SSID = "WiFi_OBDII"  # CHANGE THIS to your dongle's name
WIFI_PASS = "12345678"    # CHANGE THIS to your dongle's password (usually 1234 or 12345678)
OBD_PORT = "socket://192.168.0.10:35000"

# --- THE DASHBOARD APP ---
class CarDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup for 7-inch Screen (800x480 is standard)
        self.title("OBDII Dashboard")
        self.geometry("800x480")
        self.attributes('-fullscreen', True) # Remove this line if you want windowed mode
        ctk.set_appearance_mode("Dark")
        
        # Variables
        self.running = False
        self.connection = None
        self.csv_file = None
        self.writer = None
        
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
        """Runs nmcli command to connect to the dongle"""
        self.lbl_status.configure(text="Connecting to WiFi...", text_color="yellow")
        self.update()
        
        # Run terminal command to connect
        cmd = f'nmcli dev wifi connect "{WIFI_SSID}" password "{WIFI_PASS}"'
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
            self.connection = obd.OBD(OBD_PORT, check_voltage=False, fast=True, timeout=5, protocol="6")
            if self.connection.is_connected():
                self.lbl_status.configure(text="ECU Connected! Ready to Log.", text_color="#00FF00")
                self.btn_start.configure(state="normal")
                self.btn_wifi.configure(state="disabled") # Don't reconnect if valid
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

        # Start the worker thread (so the screen doesn't freeze)
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
        """The actual loop that runs your OBD code"""
        cmd_rpm = obd.commands.RPM
        cmd_speed = obd.commands.SPEED
        cmd_cool = obd.commands.COOLANT_TEMP
        cmd_oil = obd.commands.OIL_TEMP

        while self.running:
            # Query Data (Force=True as you requested)
            rpm = self.connection.query(cmd_rpm, force=True)
            speed = self.connection.query(cmd_speed, force=True)
            cool = self.connection.query(cmd_cool, force=True)
            oil = self.connection.query(cmd_oil, force=True)

            # Get Magnitudes
            v_rpm = int(rpm.value.magnitude) if not rpm.is_null() else 0
            v_speed = int(speed.value.magnitude) if not speed.is_null() else 0
            v_cool = int(cool.value.magnitude) if not cool.is_null() else 0
            v_oil = int(oil.value.magnitude) if not oil.is_null() else "N/A"

            # Log to CSV
            if self.writer:
                timestamp = datetime.now().strftime('%H:%M:%S')
                self.writer.writerow([timestamp, v_rpm, v_speed, v_cool, v_oil])
                self.csv_file.flush() # Ensure it saves instantly

            # Update GUI (Must use .after to be thread-safe)
            # We pass the new values to the GUI thread
            self.after(0, self.update_labels, v_rpm, v_speed, v_cool, v_oil)
            
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

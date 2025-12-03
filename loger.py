import obd
import time
import threading

# configure
PORT = "socket://192.168.0.10:35000"
TIMEOUT = 3          # seconds for library-level timeout
PRINT_HZ = 5         # how often we print to screen

# open connection (fast initialization, no voltage check)
connection = obd.OBD(PORT, check_voltage=False, fast=True, timeout=TIMEOUT, protocol="6")

if not connection.is_connected():
    print("Connection failed. Check WiFi, ignition, IP and port.")
    exit()

print("Connected (async/watch mode).")

# commands
CMD_RPM   = obd.commands.RPM
CMD_SPEED = obd.commands.SPEED
CMD_TEMP  = obd.commands.COOLANT_TEMP

# shared cache for latest good values and timestamps
latest = {
    "rpm":    {"val": None, "t": 0},
    "speed":  {"val": None, "t": 0},
    "temp":   {"val": None, "t": 0}
}

lock = threading.Lock()

# callback functions called by python-OBD when a new response arrives
def cb_rpm(resp):
    if resp is None or resp.is_null(): 
        return
    with lock:
        latest["rpm"]["val"] = resp.value.magnitude
        latest["rpm"]["t"] = time.time()

def cb_speed(resp):
    if resp is None or resp.is_null():
        return
    with lock:
        latest["speed"]["val"] = resp.value.magnitude
        latest["speed"]["t"] = time.time()

def cb_temp(resp):
    if resp is None or resp.is_null():
        return
    with lock:
        latest["temp"]["val"] = resp.value.magnitude
        latest["temp"]["t"] = time.time()

# register watchers
connection.watch(CMD_RPM, callback=cb_rpm)
connection.watch(CMD_SPEED, callback=cb_speed)
connection.watch(CMD_TEMP, callback=cb_temp)

# start background polling
connection.start()   # now the library polls these PIDs in background threads

try:
    while True:
        now = time.time()
        with lock:
            # decide whether value is fresh (not older than e.g. 2x the print interval)
            rpm_val = latest["rpm"]["val"]
            rpm_age = now - latest["rpm"]["t"] if latest["rpm"]["t"] else 999
            speed_val = latest["speed"]["val"]
            speed_age = now - latest["speed"]["t"] if latest["speed"]["t"] else 999
            temp_val = latest["temp"]["val"]
            temp_age = now - latest["temp"]["t"] if latest["temp"]["t"] else 999

        # If any value is too old, keep printing previous value (avoid zeros)
        rpm_display = rpm_val if rpm_age < 2.0 else (rpm_val if rpm_val is not None else "N/A")
        speed_display = speed_val if speed_age < 2.0 else (speed_val if speed_val is not None else "N/A")
        temp_display = temp_val if temp_age < 2.0 else (temp_val if temp_val is not None else "N/A")

        print(f"RPM: {rpm_display} | Speed: {speed_display} km/h | Temp: {temp_display} C")
        time.sleep(1.0 / PRINT_HZ)

except KeyboardInterrupt:
    print("Stopping logger...")

finally:
    connection.stop()
    connection.close()

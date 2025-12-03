import obd
import time

PORT = "socket://192.168.0.10:35000"

print("Connecting...")
connection = obd.OBD(PORT, check_voltage=False, fast=True, timeout=5, protocol="6")

if not connection.is_connected():
    print("Failed to connect.")
    exit()

print("Connected!")

cmd_rpm   = obd.commands.RPM
cmd_speed = obd.commands.SPEED
cmd_temp  = obd.commands.COOLANT_TEMP

# cache last valid values
last_rpm = 0
last_speed = 0
last_temp = 0

while True:
    rpm = connection.query(cmd_rpm, force=True)      # force=True → reduce caching delays
    speed = connection.query(cmd_speed, force=True)
    temp = connection.query(cmd_temp, force=True)

    # keep last valid values (avoid zeros)
    if not rpm.is_null():
        last_rpm = rpm.value.magnitude

    if not speed.is_null():
        last_speed = speed.value.magnitude

    if not temp.is_null():
        last_temp = temp.value.magnitude

    print(f"RPM: {last_rpm} | Speed: {last_speed} | Temp: {last_temp}")
    time.sleep(0.1)    # fast loop (10 Hz)

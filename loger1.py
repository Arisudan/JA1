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
cmd_cool  = obd.commands.COOLANT_TEMP    # 0105
cmd_oil   = obd.commands.OIL_TEMP        # 015C (not always supported)

last_rpm = 0
last_speed = 0
last_cool = 0
last_oil = "N/A"   # oil temp may not exist

while True:
    rpm = connection.query(cmd_rpm, force=True)
    speed = connection.query(cmd_speed, force=True)
    cool = connection.query(cmd_cool, force=True)
    oil  = connection.query(cmd_oil, force=True)

    if not rpm.is_null():
        last_rpm = rpm.value.magnitude

    if not speed.is_null():
        last_speed = speed.value.magnitude

    if not cool.is_null():
        last_cool = cool.value.magnitude

    # Oil temp may not exist
    if not oil.is_null():
        last_oil = oil.value.magnitude

    print(f"RPM: {last_rpm} | Speed: {last_speed} | Coolant: {last_cool}°C | Oil: {last_oil}°C")
    time.sleep(0.1)

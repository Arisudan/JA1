"""
OBD Logger Backend with Web API
Integrates with the existing OBD connection code to provide web-based dashboard
"""

import obd
import time
import json
import csv
import pandas as pd
from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
from datetime import datetime
import threading
import io
import os
from queue import Queue
import math

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend access

# Global variables for OBD data and logging
obd_data = {
    'rpm': 0,
    'speed': 0,
    'coolant': 0,
    'oil': 'N/A',
    'connected': False,
    'last_update': None
}

logged_data = []
is_logging = False
log_start_time = None
obd_connection = None
data_queue = Queue()

# OBD Configuration
PORT = "socket://192.168.0.10:35000"

def connect_obd():
    """Initialize OBD connection"""
    global obd_connection, obd_data
    
    print("Attempting to connect to OBD...")
    try:
        obd_connection = obd.OBD(PORT, check_voltage=False, fast=True, timeout=5, protocol="6")
        
        if obd_connection.is_connected():
            obd_data['connected'] = True
            print("✅ OBD Connection successful!")
            return True
        else:
            obd_data['connected'] = False
            print("❌ OBD Connection failed!")
            return False
    except Exception as e:
        print(f"❌ OBD Connection error: {e}")
        obd_data['connected'] = False
        return False

def obd_data_thread():
    """Background thread to continuously read OBD data"""
    global obd_data, obd_connection, logged_data, is_logging
    
    if not obd_connection or not obd_connection.is_connected():
        print("❌ OBD not connected, starting mock data mode")
        mock_data_mode()
        return
    
    # OBD Commands
    cmd_rpm = obd.commands.RPM
    cmd_speed = obd.commands.SPEED
    cmd_cool = obd.commands.COOLANT_TEMP
    cmd_oil = obd.commands.OIL_TEMP
    
    last_rpm = 0
    last_speed = 0
    last_cool = 0
    last_oil = "N/A"
    
    print("🔄 Starting OBD data collection...")
    
    while True:
        try:
            if obd_connection.is_connected():
                # Query OBD data
                rpm = obd_connection.query(cmd_rpm, force=True)
                speed = obd_connection.query(cmd_speed, force=True)
                cool = obd_connection.query(cmd_cool, force=True)
                oil = obd_connection.query(cmd_oil, force=True)
                
                # Update values if not null
                if not rpm.is_null():
                    last_rpm = int(rpm.value.magnitude)
                
                if not speed.is_null():
                    last_speed = int(speed.value.magnitude)
                
                if not cool.is_null():
                    last_cool = int(cool.value.magnitude)
                
                if not oil.is_null():
                    last_oil = int(oil.value.magnitude)
                
                # Update global data
                obd_data.update({
                    'rpm': last_rpm,
                    'speed': last_speed,
                    'coolant': last_cool,
                    'oil': last_oil,
                    'connected': True,
                    'last_update': datetime.now().isoformat()
                })
                
                # Log data if logging is enabled
                if is_logging:
                    log_data_point()
                
                print(f"RPM: {last_rpm} | Speed: {last_speed} | Coolant: {last_cool}°C | Oil: {last_oil}°C")
                
            else:
                obd_data['connected'] = False
                print("❌ OBD connection lost!")
                
        except Exception as e:
            print(f"❌ Error reading OBD data: {e}")
            obd_data['connected'] = False
        
        time.sleep(0.1)  # 100ms update rate

def mock_data_mode():
    """Mock data mode for testing without actual OBD connection"""
    global obd_data, is_logging
    
    print("🎭 Running in mock data mode for testing...")
    
    while True:
        try:
            # Generate realistic mock data
            current_time = time.time()
            rpm = int(800 + abs(math.sin(current_time * 0.5) * 2000))
            speed = int(max(0, 20 + math.sin(current_time * 0.3) * 40))
            coolant = int(85 + math.sin(current_time * 0.1) * 10)
            oil = int(95 + math.sin(current_time * 0.08) * 15)
            
            # Ensure realistic ranges
            rpm = max(0, min(8000, rpm))
            speed = max(0, min(200, speed))
            coolant = max(70, min(110, coolant))
            oil = max(80, min(130, oil))
            
            obd_data.update({
                'rpm': rpm,
                'speed': speed,
                'coolant': coolant,
                'oil': oil,
                'connected': True,
                'last_update': datetime.now().isoformat()
            })
            
            # Log data if logging is enabled
            if is_logging:
                log_data_point()
                
        except Exception as e:
            print(f"❌ Error in mock data mode: {e}")
        
        time.sleep(0.1)

def log_data_point():
    """Log current data point"""
    global logged_data, obd_data
    
    data_point = {
        'timestamp': datetime.now().isoformat(),
        'rpm': obd_data['rpm'],
        'speed': obd_data['speed'],
        'coolant': obd_data['coolant'],
        'oil': obd_data['oil']
    }
    logged_data.append(data_point)

# API Routes
@app.route('/api/status')
def get_status():
    """Get current OBD connection status and data"""
    return jsonify({
        **obd_data,
        'total_records': len(logged_data),
        'is_logging': is_logging,
        'log_start_time': log_start_time.isoformat() if log_start_time else None
    })

@app.route('/api/data')
def get_current_data():
    """Get current sensor readings"""
    return jsonify(obd_data)

@app.route('/api/start_logging', methods=['POST'])
def start_logging():
    """Start data logging"""
    global is_logging, log_start_time
    
    if not obd_data['connected']:
        return jsonify({'error': 'OBD not connected'}), 400
    
    is_logging = True
    log_start_time = datetime.now()
    return jsonify({'message': 'Logging started', 'start_time': log_start_time.isoformat()})

@app.route('/api/stop_logging', methods=['POST'])
def stop_logging():
    """Stop data logging"""
    global is_logging
    
    is_logging = False
    return jsonify({'message': 'Logging stopped', 'total_records': len(logged_data)})

@app.route('/api/clear_data', methods=['POST'])
def clear_data():
    """Clear logged data"""
    global logged_data, log_start_time
    
    logged_data.clear()
    log_start_time = None
    return jsonify({'message': 'Data cleared', 'total_records': 0})

@app.route('/api/download/csv')
def download_csv():
    """Download logged data as CSV"""
    if not logged_data:
        return jsonify({'error': 'No data to download'}), 400
    
    # Create CSV content
    output = io.StringIO()
    fieldnames = ['Timestamp', 'RPM', 'Speed (km/h)', 'Coolant Temperature (°C)', 'Oil Temperature (°C)']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    
    writer.writeheader()
    for row in logged_data:
        writer.writerow({
            'Timestamp': row['timestamp'],
            'RPM': row['rpm'],
            'Speed (km/h)': row['speed'],
            'Coolant Temperature (°C)': row['coolant'],
            'Oil Temperature (°C)': row['oil']
        })
    
    output.seek(0)
    
    # Create file-like object
    file_obj = io.BytesIO()
    file_obj.write(output.getvalue().encode('utf-8'))
    file_obj.seek(0)
    
    return send_file(
        file_obj,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'obd_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

@app.route('/api/download/xlsx')
def download_xlsx():
    """Download logged data as XLSX"""
    if not logged_data:
        return jsonify({'error': 'No data to download'}), 400
    
    try:
        # Convert to pandas DataFrame
        df = pd.DataFrame(logged_data)
        df.rename(columns={
            'timestamp': 'Timestamp',
            'rpm': 'RPM',
            'speed': 'Speed (km/h)',
            'coolant': 'Coolant Temperature (°C)',
            'oil': 'Oil Temperature (°C)'
        }, inplace=True)
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='OBD Log Data', index=False)
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'obd_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
        
    except ImportError:
        # Fallback to CSV if pandas/openpyxl not available
        return jsonify({'error': 'XLSX support requires pandas and openpyxl. Install with: pip install pandas openpyxl'}), 500

@app.route('/api/stats')
def get_stats():
    """Get logging statistics"""
    if not logged_data:
        return jsonify({
            'total_records': 0,
            'duration': 0,
            'start_time': None,
            'last_update': None
        })
    
    duration = 0
    if log_start_time and is_logging:
        duration = (datetime.now() - log_start_time).total_seconds()
    
    return jsonify({
        'total_records': len(logged_data),
        'duration': duration,
        'start_time': log_start_time.isoformat() if log_start_time else None,
        'last_update': obd_data.get('last_update'),
        'is_logging': is_logging
    })

@app.route('/')
def serve_dashboard():
    """Serve the HTML dashboard"""
    dashboard_path = os.path.join(os.path.dirname(__file__), 'obd_logger_dashboard.html')
    if os.path.exists(dashboard_path):
        return send_file(dashboard_path)
    else:
        return "Dashboard HTML file not found. Please ensure obd_logger_dashboard.html is in the same directory."

if __name__ == '__main__':
    print("🚗 Starting OBD Logger Backend...")
    
    # Try to connect to OBD
    connect_obd()
    
    # Start OBD data collection thread
    data_thread = threading.Thread(target=obd_data_thread, daemon=True)
    data_thread.start()
    
    print("🌐 Starting Flask web server...")
    print("📊 Dashboard available at: http://localhost:5000")
    print("🔌 API endpoints available at: http://localhost:5000/api/")
    print("\nAvailable API endpoints:")
    print("  GET  /api/status     - Connection status and current data")
    print("  GET  /api/data       - Current sensor readings")
    print("  POST /api/start_logging - Start data logging")
    print("  POST /api/stop_logging  - Stop data logging")
    print("  POST /api/clear_data    - Clear logged data")
    print("  GET  /api/download/csv  - Download data as CSV")
    print("  GET  /api/download/xlsx - Download data as XLSX")
    print("  GET  /api/stats         - Get logging statistics")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)

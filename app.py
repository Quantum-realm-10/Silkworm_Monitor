from flask import Flask, request, jsonify, render_template, Response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timezone, timedelta
import os
import csv
import io

app = Flask(__name__)
CORS(app)

# Ensure database file is placed in an absolute path within the workspace
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'instance', 'database.db')
os.makedirs(os.path.dirname(db_path), exist_ok=True)

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Timezone Helper: Indian Standard Time (IST — Asia/Kolkata, UTC+5:30) ---
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    """Returns current naive datetime adjusted to Indian Standard Time (UTC+5:30)"""
    return datetime.now(IST).replace(tzinfo=None)

def format_ist_str(dt):
    """Formats a datetime object to IST string format"""
    if dt is None:
        return "--"
    return dt.strftime("%Y-%m-%d %H:%M:%S IST")

# --- Database Model ---
class SensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    temperature = db.Column(db.Float, nullable=False)
    humidity = db.Column(db.Float, nullable=False)
    soil_moisture = db.Column(db.Float, nullable=False)
    gas_value = db.Column(db.Float, nullable=False)
    ldr_value = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=get_ist_now, nullable=False)

    def to_dict(self):
        health_info = check_health(self.temperature, self.humidity, self.gas_value)
        return {
            "id": self.id,
            "temperature": round(self.temperature, 1),
            "humidity": round(self.humidity, 1),
            "soil_moisture": round(self.soil_moisture, 1),
            "gas_value": round(self.gas_value, 1),
            "ldr_value": round(self.ldr_value, 1),
            "timestamp": format_ist_str(self.timestamp),
            "timestamp_iso": self.timestamp.isoformat(),
            "is_healthy": health_info["is_healthy"],
            "status_text": health_info["status_text"],
            "warnings": health_info["warnings"]
        }

# --- Logic: Define Healthy Silkworm Conditions ---
def check_health(temp, hum, gas):
    """
    Silkworm optimal ranges:
    - Temperature: 24.0°C - 28.0°C
    - Humidity: 70.0% - 85.0%
    - Gas Value: < 1500 (lower air pollution is safer)
    """
    warnings = []
    if temp < 24.0:
        warnings.append("Low Temperature (<24°C): May slow silkworm growth.")
    elif temp > 28.0:
        warnings.append("High Temperature (>28°C): Risk of heat stress.")

    if hum < 70.0:
        warnings.append("Low Humidity (<70%): Mulberry leaves dry out quickly.")
    elif hum > 85.0:
        warnings.append("High Humidity (>85%): Promotes fungal & disease growth.")

    if gas > 1500:
        warnings.append("High Gas/Air Pollution (>1500): Toxic air quality detected.")

    is_healthy = len(warnings) == 0
    status_text = "Optimal Conditions" if is_healthy else "Suboptimal Conditions"

    return {
        "is_healthy": is_healthy,
        "status_text": status_text,
        "warnings": warnings
    }

# --- Logic: Delete Data Older Than 7 Days ---
def cleanup_data():
    cutoff = get_ist_now() - timedelta(days=7)
    SensorData.query.filter(SensorData.timestamp < cutoff).delete()
    db.session.commit()

with app.app_context():
    db.create_all()

# --- Page Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history_page():
    return render_template('history.html')

# --- API Endpoints ---
@app.route('/api/data', methods=['POST'])
def receive_data():
    """Receives JSON data from ESP32 or manual simulation, persists to SQLite DB, returns health status."""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON format"}), 400

    try:
        temp = float(data.get('temperature', 0))
        hum = float(data.get('humidity', 0))
        soil = float(data.get('soil_moisture', 0))
        gas = float(data.get('gas_value', 0))
        ldr = float(data.get('ldr_value', 0))

        # Create entry with current IST timestamp
        new_entry = SensorData(
            temperature=temp,
            humidity=hum,
            soil_moisture=soil,
            gas_value=gas,
            ldr_value=ldr,
            timestamp=get_ist_now()
        )
        
        db.session.add(new_entry)
        db.session.commit()
        
        # Cleanup old data (> 7 days)
        cleanup_data()

        health_info = check_health(temp, hum, gas)
        buzzer_status = not health_info["is_healthy"]

        return jsonify({
            "status": "success",
            "message": "Data saved successfully to database",
            "entry": new_entry.to_dict(),
            "buzzer": buzzer_status
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error saving sensor data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/latest')
def get_latest():
    """Returns the most recent sensor reading from the database."""
    latest = SensorData.query.order_by(SensorData.id.desc()).first()
    if not latest:
        return jsonify({"message": "No data recorded yet", "has_data": False})
    
    res = latest.to_dict()
    res["has_data"] = True
    return jsonify(res)

@app.route('/api/history')
def get_history_api():
    """
    Returns JSON history with pagination, search, filtering, and summary metrics.
    Query params:
    - search: text query (matches timestamp, temp, hum, status)
    - status: 'all' | 'healthy' | 'alert'
    - page: int (default 1)
    - per_page: int (default 20)
    """
    search = request.args.get('search', '').strip().lower()
    status_filter = request.args.get('status', 'all').lower()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = SensorData.query.order_by(SensorData.id.desc())
    all_records = query.all()
    
    # Calculate overall analytics across all records
    total_records = len(all_records)
    if total_records > 0:
        avg_temp = round(sum(r.temperature for r in all_records) / total_records, 1)
        avg_hum = round(sum(r.humidity for r in all_records) / total_records, 1)
        alert_count = sum(1 for r in all_records if not check_health(r.temperature, r.humidity, r.gas_value)["is_healthy"])
    else:
        avg_temp = 0
        avg_hum = 0
        alert_count = 0

    # Filter in memory for maximum search flexibility
    filtered = []
    for r in all_records:
        r_dict = r.to_dict()
        
        # Status filtering
        if status_filter == 'healthy' and not r_dict['is_healthy']:
            continue
        if status_filter == 'alert' and r_dict['is_healthy']:
            continue

        # Search query matching
        if search:
            match_string = f"{r_dict['timestamp']} {r_dict['temperature']} {r_dict['humidity']} {r_dict['gas_value']} {r_dict['soil_moisture']} {r_dict['ldr_value']} {r_dict['status_text']}".lower()
            if search not in match_string:
                continue

        filtered.append(r_dict)

    total_filtered = len(filtered)
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_items = filtered[start_idx:end_idx]

    return jsonify({
        "status": "success",
        "items": paginated_items,
        "total_records": total_records,
        "total_filtered": total_filtered,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total_filtered + per_page - 1) // per_page),
        "analytics": {
            "avg_temp": avg_temp,
            "avg_hum": avg_hum,
            "alert_count": alert_count
        }
    })

@app.route('/api/simulate', methods=['POST'])
def simulate_data():
    """Generates and saves a simulated sensor reading directly from the web interface."""
    import random
    data = request.get_json(silent=True) or {}
    
    temp = float(data.get('temperature', round(random.uniform(22.0, 30.0), 1)))
    hum = float(data.get('humidity', round(random.uniform(65.0, 90.0), 1)))
    soil = float(data.get('soil_moisture', round(random.uniform(30.0, 70.0), 1)))
    gas = float(data.get('gas_value', round(random.uniform(400.0, 1600.0), 1)))
    ldr = float(data.get('ldr_value', round(random.uniform(200.0, 800.0), 1)))

    new_entry = SensorData(
        temperature=temp,
        humidity=hum,
        soil_moisture=soil,
        gas_value=gas,
        ldr_value=ldr,
        timestamp=get_ist_now()
    )
    db.session.add(new_entry)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": "Simulated data added successfully",
        "entry": new_entry.to_dict()
    }), 201

@app.route('/api/export-csv')
def export_csv():
    """Exports all sensor records from the database as a CSV download with IST timestamps."""
    records = SensorData.query.order_by(SensorData.id.desc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Record ID', 'IST Timestamp', 'Temperature (°C)', 'Humidity (%)', 'Soil Moisture', 'Gas Value', 'Light (LDR)', 'Silkworm Health Status'])

    for r in records:
        health = check_health(r.temperature, r.humidity, r.gas_value)
        status = "Optimal" if health["is_healthy"] else "Suboptimal/Alert"
        writer.writerow([
            r.id,
            format_ist_str(r.timestamp),
            r.temperature,
            r.humidity,
            r.soil_moisture,
            r.gas_value,
            r.ldr_value,
            status
        ])

    output.seek(0)
    filename = f"silkworm_sensor_history_ist_{get_ist_now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
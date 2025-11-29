from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta
import os

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Database Model ---
class SensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    temperature = db.Column(db.Float)
    humidity = db.Column(db.Float)
    soil_moisture = db.Column(db.Float)
    gas_value = db.Column(db.Float)
    ldr_value = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.now) # stored as DateTime object

# --- Logic: Define Healthy Silkworm Conditions ---
def check_health(temp, hum, gas):
    # Silkworms generally thrive in 24-28°C and 70-85% Humidity
    if (temp < 24 or temp > 29): return False
    if (hum < 60 or hum > 90): return False
    if (gas > 1500): return False # Assuming high value = bad air
    return True

# --- Logic: Delete Old Data ---
def cleanup_data():
    cutoff = datetime.now() - timedelta(days=7)
    # Delete records older than 7 days
    SensorData.query.filter(SensorData.timestamp < cutoff).delete()
    db.session.commit()

with app.app_context():
    db.create_all()

# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history():
    # Fetch last 100 records for the table
    data = SensorData.query.order_by(SensorData.id.desc()).limit(100).all()
    return render_template('history.html', data=data)

@app.route('/api/data', methods=['POST'])
def receive_data():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error"}), 400

    try:
        # 1. Parse Data
        temp = float(data.get('temperature', 0))
        hum = float(data.get('humidity', 0))
        soil = float(data.get('soil_moisture', 0))
        gas = float(data.get('gas_value', 0))
        ldr = float(data.get('ldr_value', 0))

        # 2. Store Data
        # 1. Get server time (UTC)
    utc_now = datetime.now()
    
    # 2. Add 5 hours and 30 minutes for IST (India Standard Time)
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    
    # 3. Save the corrected time
    new_entry = SensorData(
        temperature=temperature,
        humidity=humidity,
        soil_moisture=soil_moisture,
        gas_value=gas_value,
        ldr_value=ldr_value,
        flame_detected=flame_detected,
        timestamp=ist_now.strftime("%Y-%m-%d %H:%M:%S") # <--- Fixed Timestamp
    )
        db.session.add(new_entry)
        
        # 3. Run Cleanup (remove > 7 days old)
        cleanup_data()
        
        db.session.commit()

        # 4. Check Health & Decide Buzzer Status
        is_healthy = check_health(temp, hum, gas)
        buzzer_status = not is_healthy # If NOT healthy, Buzzer = True

        # 5. Send command back to ESP32
        return jsonify({
            "status": "success",
            "buzzer": buzzer_status
        }), 200

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/latest')
def get_latest():
    latest = SensorData.query.order_by(SensorData.id.desc()).first()
    if not latest:
        return jsonify({"message": "No data"})
    
    # Check health again for frontend display
    is_healthy = check_health(latest.temperature, latest.humidity, latest.gas_value)

    return jsonify({
        "temperature": latest.temperature,
        "humidity": latest.humidity,
        "soil_moisture": latest.soil_moisture,
        "gas_value": latest.gas_value,
        "ldr_value": latest.ldr_value,
        "timestamp": latest.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "is_healthy": is_healthy
    })

if __name__ == '__main__':
    # Use '0.0.0.0' to be accessible by ESP32 on the same WiFi

    app.run(host='0.0.0.0', port=5000, debug=True)

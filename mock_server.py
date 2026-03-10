from flask import Flask, request, jsonify, send_file
import os
import db_mock

app = Flask(__name__)

@app.route("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "mock_form.html"))

@app.route("/enroll", methods=["POST"])
def enroll():
    data = request.json
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    ssn = data.get("ssn")

    if not all([first_name, last_name, ssn]):
        return jsonify({"error": "Missing required fields"}), 400

    row_id = db_mock.insert_customer(first_name, last_name, ssn)
    if not row_id:
        return jsonify({"error": "SSN already exists"}), 409
    
    return jsonify({"success": True, "id": row_id})

if __name__ == "__main__":
    db_mock.init_db()
    app.run(port=5000)

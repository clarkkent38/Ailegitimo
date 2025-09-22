import os
from flask import Flask, jsonify
from flask_cors import CORS

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GCP_CREDENTIALS_JSON = os.environ.get("GCP_CREDENTIALS_JSON")

app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET", "POST"])
def health():
    return jsonify({
        "status": "healthy", 
        "message": "Flask app is running",
        "gemini_configured": bool(GEMINI_API_KEY),
        "gcp_configured": bool(GCP_CREDENTIALS_JSON)
    })

# Handler for Vercel
def handler(event, context):
    """Handler for Vercel serverless deployment"""
    with app.app_context():
        return app.full_dispatch_request()

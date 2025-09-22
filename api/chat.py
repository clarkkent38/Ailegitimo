import os
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

@app.route('/', methods=['POST', 'GET'])
def chat():
    """Chat endpoint handler"""
    print("💬 /api/chat endpoint called")
    
    if request.method == 'GET':
        return jsonify({"message": "Chat endpoint is working. Send POST request with history."})
    
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400
            
        history = data.get('history', [])
        language = data.get('language', 'English')
        
        if not history:
            return jsonify({"error": "No chat history provided"}), 400
        
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        # Start chat with previous history
        chat = gemini_model.start_chat(history=history[:-1])
        user_question = history[-1]['parts'][0]['text']
        
        prompt = f"Based on the document context I provided earlier, answer this question in {language}: {user_question}"
        response = chat.send_message(prompt)
        
        return jsonify({"response": response.text})
        
    except Exception as e:
        print(f"❌ Error in /api/chat: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Handler for Vercel
def handler(event, context):
    """Handler for Vercel serverless deployment"""
    with app.app_context():
        return app.full_dispatch_request()

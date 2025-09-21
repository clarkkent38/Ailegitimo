import os
import io
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import vision, storage, bigquery
from google.oauth2 import service_account
import PyPDF2
import docx
import datetime
import uuid

# --- Flask App Initialization ---
app = Flask(__name__)
CORS(app)

# --- CONFIGURATION from Environment Variables ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET")
BIGQUERY_TABLE = os.environ.get("BIGQUERY_TABLE")
GCP_CREDENTIALS_JSON = os.environ.get("GCP_CREDENTIALS_JSON")

# Handle credentials safely
credentials = None
if GCP_CREDENTIALS_JSON:
    try:
        credentials_info = json.loads(GCP_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        print("✅ GCP Credentials loaded successfully")
    except Exception as e:
        print(f"⚠️ GCP Credentials error: {e}")

# --- Knowledge Base Loading ---
LEGAL_KNOWLEDGE_BASE = """
This is a placeholder for legal knowledge base. 
In a real deployment, you would load your BNS and Constitution files here.
For now, the system will work with general legal analysis.
"""

# --- Helper Functions ---
def extract_text_from_file_in_memory(file_storage, filename):
    ext = os.path.splitext(filename)[1].lower()
    text = ""
    file_stream = io.BytesIO(file_storage.read())
    file_storage.seek(0)
    
    if ext == ".txt":
        text = file_stream.read().decode('utf-8')
    elif ext == ".pdf":
        try:
            reader = PyPDF2.PdfReader(file_stream)
            for page in reader.pages:
                text += page.extract_text() or ""
        except Exception as e:
            raise Exception(f"PDF processing error: {e}")
    elif ext == ".docx":
        try:
            doc = docx.Document(file_stream)
            for para in doc.paragraphs: 
                text += para.text + "\n"
        except Exception as e:
            raise Exception(f"DOCX processing error: {e}")
    elif ext in [".png", ".jpg", ".jpeg"]:
        # For images, we'll skip Vision API for now to avoid credential issues
        text = "Image processing temporarily disabled. Please upload text documents."
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    
    return text

# --- Routes WITHOUT /api prefix ---

@app.route('/analyze', methods=['POST'])
def handle_analysis():
    print("🚀 /analyze endpoint called")
    
    # Basic configuration check
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500
    
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file part in request"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
            
        language = request.form.get('language', 'English')
        print(f"📄 Processing: {file.filename}, Language: {language}")
        
        # Extract text from file
        file_bytes = file.read()
        file.seek(0)
        document_text = extract_text_from_file_in_memory(file, file.filename)
        
        if not document_text.strip():
            return jsonify({"error": "No text could be extracted from the file"}), 400
        
        print(f"✅ Text extraction successful: {len(document_text)} characters")
        
        # Configure Gemini
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        # Generate AI analysis with simplified prompt
        initial_prompt = f"""You are an expert AI legal assistant. Analyze the user's document in {language}. 

Provide your analysis in this format:
### Summary
### Risk Analysis  
### Key Clauses & Legal Connections
### Potential Mistakes & Ambiguities

--- USER'S DOCUMENT ---
{document_text[:15000]}  
--- END DOCUMENT ---"""
        
        response = gemini_model.generate_content(initial_prompt)
        print("✅ AI analysis generated successfully")
        
        # Try cloud storage (optional)
        document_id = str(uuid.uuid4())
        if credentials and GCP_PROJECT_ID and GCS_BUCKET_NAME:
            try:
                storage_client = storage.Client(project=GCP_PROJECT_ID, credentials=credentials)
                blob_name = f"uploads/{document_id}/{file.filename}"
                bucket = storage_client.bucket(GCS_BUCKET_NAME)
                blob = bucket.blob(blob_name)
                blob.upload_from_string(file_bytes, content_type=file.content_type or 'application/octet-stream')
                print(f"✅ Uploaded to GCS: gs://{GCS_BUCKET_NAME}/{blob_name}")
            except Exception as e:
                print(f"⚠️ GCS upload failed: {e}")
        
        return jsonify({
            "analysis": response.text, 
            "documentText": document_text,
            "documentId": document_id,
            "status": "success"
        })
        
    except Exception as e:
        print(f"❌ Error in /analyze: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/chat', methods=['POST'])
def handle_chat():
    print("💬 /chat endpoint called")
    
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
        
        chat = gemini_model.start_chat(history=history[:-1])
        user_question = history[-1]['parts'][0]['text']
        prompt = f"Based on the document context, answer this question in {language}: {user_question}"
        response = chat.send_message(prompt)
        
        return jsonify({"response": response.text})
        
    except Exception as e:
        print(f"❌ Error in /chat: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy", 
        "message": "Flask app is running",
        "gemini_configured": bool(GEMINI_API_KEY),
        "gcp_configured": bool(credentials)
    })

@app.route('/')
def root():
    return jsonify({
        "message": "AI Legitimo API is running",
        "available_endpoints": ["/health", "/analyze", "/chat"]
    })

if __name__ == '__main__':
    app.run(debug=True)

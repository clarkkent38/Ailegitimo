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
# The filename `app.py` is the entry point for Vercel.
# The variable `app` (a Flask instance) is what Vercel serves.
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
credentials_info = None
credentials = None
if GCP_CREDENTIALS_JSON:
    try:
        credentials_info = json.loads(GCP_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
    except json.JSONDecodeError:
        print("ERROR: Invalid GCP_CREDENTIALS_JSON format")
    except Exception as e:
        print(f"ERROR: Failed to create credentials: {e}")

# --- Knowledge Base Loading ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
BNS_KNOWLEDGE_BASE = ""
INDIAN_CONSTITUTION_KNOWLEDGE_BASE = ""
try:
    with open(os.path.join(APP_DIR, '..', 'bns_knowledge_base.txt'), 'r', encoding='utf-8') as f:
        BNS_KNOWLEDGE_BASE = f.read()
except FileNotFoundError:
    print("WARNING: bns_knowledge_base.txt not found.")
try:
    with open(os.path.join(APP_DIR, '..', 'indian_constitution.txt'), 'r', encoding='utf-8') as f:
        INDIAN_CONSTITUTION_KNOWLEDGE_BASE = f.read()
except FileNotFoundError:
    print("WARNING: indian_constitution.txt not found.")
LEGAL_KNOWLEDGE_BASE = BNS_KNOWLEDGE_BASE + "\n\n" + INDIAN_CONSTITUTION_KNOWLEDGE_BASE

# --- Helper Functions (In-Memory Processing) ---
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
        if not credentials:
            raise Exception("Google Cloud credentials not configured for image processing")
        try:
            vision_client = vision.ImageAnnotatorClient(credentials=credentials)
            content = file_stream.read()
            image = vision.Image(content=content)
            response = vision_client.text_detection(image=image)
            if response.error.message: 
                raise Exception(f"Vision API Error: {response.error.message}")
            text = response.text_annotations[0].description if response.text_annotations else ""
        except Exception as e:
            raise Exception(f"Image processing error: {e}")
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    return text

# --- API Routes ---

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Flask app is running"})

# Analysis endpoint - Vercel routes /api/analyze to this
@app.route('/analyze', methods=['POST'])
def handle_analysis():
    print("=== Analysis endpoint hit ===")
    
    # Check configuration
    missing_configs = []
    if not GEMINI_API_KEY: missing_configs.append("GEMINI_API_KEY")
    if not GCP_PROJECT_ID: missing_configs.append("GCP_PROJECT_ID")
    if not GCS_BUCKET_NAME: missing_configs.append("GCS_BUCKET_NAME")
    if not BIGQUERY_DATASET: missing_configs.append("BIGQUERY_DATASET")
    if not BIGQUERY_TABLE: missing_configs.append("BIGQUERY_TABLE")
    if not credentials: missing_configs.append("GCP_CREDENTIALS_JSON")
    
    if missing_configs:
        return jsonify({
            "error": f"Missing environment variables: {', '.join(missing_configs)}"
        }), 500
    
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
            
        language = request.form.get('language', 'English')
        print(f"Processing file: {file.filename}, Language: {language}")
        
        # Extract text from file
        file_bytes = file.read()
        file.seek(0)
        document_text = extract_text_from_file_in_memory(file, file.filename)
        
        if not document_text.strip():
            return jsonify({"error": "No text could be extracted from the file"}), 400
        
        print(f"Extracted text length: {len(document_text)}")
        
        # Initialize Google Cloud clients
        storage_client = storage.Client(project=GCP_PROJECT_ID, credentials=credentials)
        bq_client = bigquery.Client(project=GCP_PROJECT_ID, credentials=credentials)
        
        # Configure Gemini
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        # Upload to GCS
        document_id = str(uuid.uuid4())
        blob_name = f"uploads/{document_id}/{file.filename}"
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(file_bytes, content_type=file.content_type or 'application/octet-stream')
        gcs_path = f"gs://{GCS_BUCKET_NAME}/{blob_name}"
        print(f"Uploaded to GCS: {gcs_path}")
        
        # Log to BigQuery
        metadata = {
            "document_id": document_id, 
            "filename": file.filename,
            "upload_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "UPLOADED", 
            "storage_path": gcs_path,
        }
        table_id = f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET}.{BIGQUERY_TABLE}"
        bq_client.insert_rows_json(table_id, [metadata])
        print("Logged to BigQuery")
        
        # Generate AI analysis
        initial_prompt = f"""You are an expert AI legal assistant. Analyze the user's document based on the provided legal knowledge base in {language}. The output must strictly follow this format: ### Summary, ### Risk Analysis, ### Key Clauses & Legal Connections, ### Potential Mistakes & Ambiguities.
When generating '### Key Clauses & Legal Connections', cite the specific section or article number.
--- LEGAL KNOWLEDGE BASE ---
{LEGAL_KNOWLEDGE_BASE}
--- END KNOWLEDGE BASE ---
--- USER'S DOCUMENT ---
{document_text}
--- END DOCUMENT ---"""
        
        response = gemini_model.generate_content(initial_prompt)
        print("Generated AI analysis")
        
        return jsonify({
            "analysis": response.text, 
            "documentText": document_text,
            "documentId": document_id
        })
        
    except Exception as e:
        print(f"Error in /analyze: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Chat endpoint - Vercel routes /api/chat to this
@app.route('/chat', methods=['POST'])
def handle_chat():
    print("=== Chat endpoint hit ===")
    
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
        
        print(f"Processing chat with {len(history)} messages in {language}")
        
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        chat = gemini_model.start_chat(history=history[:-1])
        user_question = history[-1]['parts'][0]['text']
        prompt = f"Based on the document context, answer this in {language}: {user_question}"
        response = chat.send_message(prompt)
        
        print("Generated chat response")
        return jsonify({"response": response.text})
        
    except Exception as e:
        print(f"Error in /chat: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Fallback route for debugging
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return jsonify({
        "message": f"Flask app is running. Path requested: /{path}",
        "available_endpoints": ["/health", "/analyze", "/chat"]
    })

if __name__ == '__main__':
    app.run(debug=True)

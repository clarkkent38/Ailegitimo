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

# --- ✅ FIX: CONFIGURATION from Environment Variables ---
# Load secrets securely from Vercel's environment variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET")
BIGQUERY_TABLE = os.environ.get("BIGQUERY_TABLE")

# --- ✅ FIX: Handle JSON credentials from a multi-line environment variable ---
GCP_CREDENTIALS_JSON = os.environ.get("GCP_CREDENTIALS_JSON")
credentials_info = json.loads(GCP_CREDENTIALS_JSON) if GCP_CREDENTIALS_JSON else None
credentials = service_account.Credentials.from_service_account_info(credentials_info) if credentials_info else None

# --- ✅ FIX: Knowledge Base Loading with correct serverless paths ---
# The 'api' folder is the current directory, so we go up one level to find the text files.
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

# --- ✅ FIX: Helper Functions Modified for In-Memory Processing ---
def extract_text_from_file_in_memory(file_storage, filename):
    ext = os.path.splitext(filename)[1].lower()
    text = ""
    # Create an in-memory binary stream from the file's content
    file_stream = io.BytesIO(file_storage.read())
    file_storage.seek(0) # Reset file pointer for potential re-reads
    
    vision_client = vision.ImageAnnotatorClient(credentials=credentials)
    
    if ext == ".txt":
        text = file_stream.read().decode('utf-8')
    elif ext == ".pdf":
        reader = PyPDF2.PdfReader(file_stream)
        for page in reader.pages: text += page.extract_text() or ""
    elif ext == ".docx":
        doc = docx.Document(file_stream)
        for para in doc.paragraphs: text += para.text + "\n"
    elif ext in [".png", ".jpg", ".jpeg"]:
        content = file_stream.read()
        image = vision.Image(content=content)
        response = vision_client.text_detection(image=image)
        if response.error.message: raise Exception(f"Vision API Error: {response.error.message}")
        text = response.text_annotations[0].description if response.text_annotations else ""
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    return text

# --- API Routes ---
@app.route('/api/analyze', methods=['POST'])
def handle_analysis():
    # Check if all environment variables are loaded
    if not all([GEMINI_API_KEY, credentials, GCP_PROJECT_ID, GCS_BUCKET_NAME, BIGQUERY_DATASET, BIGQUERY_TABLE]):
        return jsonify({"error": "Server-side configuration is incomplete. Please check Vercel environment variables."}), 500

    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        file = request.files['file']
        language = request.form.get('language', 'English')
        
        # Process file in memory
        file_bytes = file.read()
        file.seek(0) 
        document_text = extract_text_from_file_in_memory(file, file.filename)
        
        # Initialize clients with credentials
        storage_client = storage.Client(project=GCP_PROJECT_ID, credentials=credentials)
        bq_client = bigquery.Client(project=GCP_PROJECT_ID, credentials=credentials)
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')

        # GCS and BigQuery operations
        document_id = str(uuid.uuid4())
        blob_name = f"uploads/{document_id}/{file.filename}"
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(file_bytes, content_type=file.content_type)
        gcs_path = f"gs://{GCS_BUCKET_NAME}/{blob_name}"

        metadata = {
            "document_id": document_id, "filename": file.filename,
            "upload_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "UPLOADED", "storage_path": gcs_path,
        }
        table_id = f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET}.{BIGQUERY_TABLE}"
        bq_client.insert_rows_json(table_id, [metadata])

        # Generate AI content
        initial_prompt = f"""You are an expert AI legal assistant. Analyze the user's document based on the provided legal knowledge base in {language}. The output must strictly follow this format: ### Summary, ### Risk Analysis, ### Key Clauses & Legal Connections, ### Potential Mistakes & Ambiguities.
When generating '### Key Clauses & Legal Connections', cite the specific section or article number.
--- LEGAL KNOWLEDGE BASE ---
{LEGAL_KNOWLEDGE_BASE}
--- END KNOWLEDGE BASE ---
--- USER'S DOCUMENT ---
{document_text}
--- END DOCUMENT ---"""
        response = gemini_model.generate_content(initial_prompt)
        
        return jsonify({"analysis": response.text, "documentText": document_text})

    except Exception as e:
        print(f"Error in /api/analyze: {e}") # Log the actual error to Vercel logs
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def handle_chat():
    try:
        data = request.get_json()
        history = data['history']
        language = data.get('language', 'English')
        
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        chat = gemini_model.start_chat(history=history[:-1])
        user_question = history[-1]['parts'][0]['text']
        prompt = f"Based on the document context, answer this in {language}: {user_question}"
        response = chat.send_message(prompt)
        
        return jsonify({"response": response.text})
    except Exception as e:
        print(f"Error in /api/chat: {e}") # Log the actual error
        return jsonify({"error": str(e)}), 500


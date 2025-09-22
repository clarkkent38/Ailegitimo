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

app = Flask(__name__)
CORS(app)

# --- Environment Variables (Set these in Vercel Dashboard) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID") 
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
BIGQUERY_DATASET = os.environ.get("BIGQUERY_DATASET")
GCP_CREDENTIALS_JSON = os.environ.get("GCP_CREDENTIALS_JSON")

# Initialize GCP credentials from environment
credentials = None
if GCP_CREDENTIALS_JSON:
    try:
        credentials_info = json.loads(GCP_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        print("✅ GCP Credentials loaded successfully")
    except Exception as e:
        print(f"⚠️ GCP Credentials error: {e}")

# --- Embedded Knowledge Base (instead of file reading) ---
LEGAL_KNOWLEDGE_BASE = """
--- BHARATIYA NYAYA SANHITA (BNS) SUMMARY ---
Key sections include:
- Section 101: General principles of criminal liability
- Section 124: Murder and culpable homicide
- Section 303: Theft and related offenses
- Section 420: Cheating and fraud

--- INDIAN CONSTITUTION SUMMARY ---
Key articles include:
- Article 14: Right to Equality
- Article 19: Right to Freedom of Speech
- Article 21: Right to Life and Personal Liberty
- Article 32: Right to Constitutional Remedies

Note: This is a simplified knowledge base. In production, you would embed the full legal texts.
"""

def initialize_clients():
    """Initialize Google Cloud clients"""
    if not credentials:
        return None
    
    try:
        clients = {
            "vision": vision.ImageAnnotatorClient(credentials=credentials),
            "storage": storage.Client(project=GCP_PROJECT_ID, credentials=credentials),
            "bigquery": bigquery.Client(project=GCP_PROJECT_ID, credentials=credentials)
        }
        return clients
    except Exception as e:
        print(f"⚠️ Could not initialize all Google Cloud clients: {e}")
        return None

def extract_text_from_file_in_memory(file_storage, filename):
    """Extract text from uploaded file"""
    ext = os.path.splitext(filename)[1].lower()
    text = ""
    file_stream = io.BytesIO(file_storage.read())
    
    try:
        if ext == ".txt":
            file_stream.seek(0)
            text = file_stream.read().decode('utf-8')
        elif ext == ".pdf":
            file_stream.seek(0)
            reader = PyPDF2.PdfReader(file_stream)
            for page in reader.pages:
                text += page.extract_text() or ""
        elif ext == ".docx":
            file_stream.seek(0)
            doc = docx.Document(file_stream)
            for para in doc.paragraphs:
                text += para.text + "\n"
        elif ext in [".png", ".jpg", ".jpeg"]:
            # For images, use Vision API if available
            if credentials:
                try:
                    vision_client = vision.ImageAnnotatorClient(credentials=credentials)
                    file_stream.seek(0)
                    content = file_stream.read()
                    image = vision.Image(content=content)
                    response = vision_client.text_detection(image=image)
                    if response.text_annotations:
                        text = response.text_annotations[0].description
                    else:
                        text = "No text found in image"
                except Exception as e:
                    text = f"Image processing failed: {e}"
            else:
                text = "Image processing requires GCP credentials"
        else:
            raise ValueError(f"Unsupported file type: {ext}")
            
        return text
    except Exception as e:
        raise Exception(f"Text extraction failed: {e}")

def upload_to_gcs_from_memory(file_bytes, filename, document_id):
    """Upload file to Google Cloud Storage"""
    if not credentials or not GCP_PROJECT_ID or not GCS_BUCKET_NAME:
        return None
        
    try:
        storage_client = storage.Client(project=GCP_PROJECT_ID, credentials=credentials)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob_name = f"uploads/{document_id}/{filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(file_bytes)
        return f"gs://{GCS_BUCKET_NAME}/{blob_name}"
    except Exception as e:
        print(f"⚠️ GCS upload failed: {e}")
        return None

def log_to_bigquery(metadata):
    """Log metadata to BigQuery"""
    if not credentials or not GCP_PROJECT_ID or not BIGQUERY_DATASET:
        return False
        
    try:
        bq_client = bigquery.Client(project=GCP_PROJECT_ID, credentials=credentials)
        table_id = f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET}.document_metadata"
        errors = bq_client.insert_rows_json(table_id, [metadata])
        return len(errors) == 0
    except Exception as e:
        print(f"⚠️ BigQuery logging failed: {e}")
        return False

@app.route('/', methods=['POST', 'GET'])
def analyze():
    """Main analyze endpoint handler"""
    print("🚀 /api/analyze endpoint called")
    
    if request.method == 'GET':
        return jsonify({"message": "Analyze endpoint is working. Send POST request with file."})
    
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500

    if 'file' not in request.files:
        return jsonify({"error": "No file part in request"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    try:
        language = request.form.get('language', 'English')
        print(f"📄 Processing: {file.filename}, Language: {language}")
        
        # Read file into memory
        file_bytes = file.read()
        file.seek(0)
        
        # Extract text from file
        document_text = extract_text_from_file_in_memory(file, file.filename)
        
        if not document_text.strip():
            return jsonify({"error": "No text could be extracted from the file"}), 400
        
        print(f"✅ Text extraction successful: {len(document_text)} characters")
        
        # Configure Gemini
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        # Generate AI analysis
        initial_prompt = f"""You are an expert Indian legal assistant. Analyze the user's document based on the provided legal knowledge base. Provide a structured breakdown in {language}. The output must strictly follow this format: ### Summary, ### Risk Analysis, ### Key Clauses & Legal Connections, ### Potential Mistakes & Ambiguities.

When generating the '### Key Clauses & Legal Connections' section, you MUST refer to the following legal texts to identify relevant clauses and articles. Cite the specific section or article number (e.g., BNS Section 101, Article 14 of the Indian Constitution).

--- LEGAL KNOWLEDGE BASE ---
{LEGAL_KNOWLEDGE_BASE}
--- END KNOWLEDGE BASE ---

--- USER'S DOCUMENT ---
{document_text[:15000]}
--- END DOCUMENT ---"""
        
        response = gemini_model.generate_content(initial_prompt)
        print("✅ AI analysis generated successfully")
        
        # Optional: Cloud storage and logging
        document_id = str(uuid.uuid4())
        
        # Try to upload to GCS
        gcs_path = upload_to_gcs_from_memory(file_bytes, file.filename, document_id)
        
        # Try to log metadata
        if gcs_path:
            metadata = {
                "document_id": document_id,
                "filename": file.filename,
                "file_type": os.path.splitext(file.filename)[1].lower(),
                "file_size": len(file_bytes),
                "upload_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": "UPLOADED",
                "storage_path": gcs_path,
            }
            log_to_bigquery(metadata)
        
        return jsonify({
            "analysis": response.text,
            "documentText": document_text,
            "documentId": document_id,
            "status": "success"
        })
        
    except Exception as e:
        print(f"❌ Error in /api/analyze: {str(e)}")
        return jsonify({"error": str(e)}), 500

# This is the handler that Vercel will call
def handler(event, context):
    """Handler for Vercel serverless deployment"""
    with app.app_context():
        return app.full_dispatch_request()

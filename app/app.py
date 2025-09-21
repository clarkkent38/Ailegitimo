# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from google.cloud import vision, storage, bigquery
import PyPDF2
import docx
import os
import uuid
import datetime
import tempfile

app = Flask(__name__)
CORS(app) # Enable Cross-Origin Resource Sharing

# --- SINGLE CONFIGURATION BLOCK ---
# ALL YOUR SETTINGS ARE NOW IN ONE PLACE.
# PASTE YOUR CREDENTIALS AND PROJECT DETAILS DIRECTLY INTO THE CODE HERE.

# 1. Gemini API Key
GEMINI_API_KEY = "AIzaSyAm9w7IerE1lMUsEgfdzm07V5p_2wYoAoI"

# 2. FULL path to your Google Cloud credentials JSON file.
#    Use a raw string (the 'r' before the quotes) to handle backslashes correctly.
CREDENTIALS_PATH = r"legal-ai-470918-952813a27573.json"

# 3. Google Cloud Project Details
GCP_PROJECT_ID = "legal-ai-470918"
GCS_BUCKET_NAME = "legal-ai-470918-legal-documents"
BIGQUERY_DATASET = "legal-ai-470918-dataset"
BIGQUERY_TABLE = "legal-ai-470918-table"


# --- KNOWLEDGE BASE LOADING ---
# This section reads your legal text files when the server starts.
BNS_KNOWLEDGE_BASE = ""
INDIAN_CONSTITUTION_KNOWLEDGE_BASE = ""

try:
    with open('bns_knowledge_base.txt', 'r', encoding='utf-8') as f:
        BNS_KNOWLEDGE_BASE = f.read()
    print("✅ BNS knowledge base loaded successfully.")
except FileNotFoundError:
    print("⚠️ WARNING: bns_knowledge_base.txt not found. Legal connections may be less specific.")

try:
    with open('indian_constitution.txt', 'r', encoding='utf-8') as f:
        INDIAN_CONSTITUTION_KNOWLEDGE_BASE = f.read()
    print("✅ Indian Constitution knowledge base loaded successfully.")
except FileNotFoundError:
    print("⚠️ WARNING: indian_constitution.txt not found. Legal connections may be less specific.")

# Combine both knowledge bases into a single string for the prompt
LEGAL_KNOWLEDGE_BASE = f"""
--- BHARATIYA NYAYA SANHITA (BNS) ---
{BNS_KNOWLEDGE_BASE}

--- INDIAN CONSTITUTION ---
{INDIAN_CONSTITUTION_KNOWLEDGE_BASE}
"""


# --- HELPER FUNCTIONS ---

def initialize_clients():
    """Initializes all necessary API clients using the hardcoded configuration."""
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = CREDENTIALS_PATH
    clients = {}
    try:
        clients["vision"] = vision.ImageAnnotatorClient()
        clients["storage"] = storage.Client(project=GCP_PROJECT_ID)
        clients["bigquery"] = bigquery.Client(project=GCP_PROJECT_ID)
        print("✅ Google Cloud clients initialized successfully.")
        return clients
    except Exception as e:
        print(f"❌ FATAL: Could not initialize Google Cloud clients: {e}")
        return None

def upload_to_gcs(local_file_path, storage_client, document_id, original_filename):
    """Uploads a file to GCS."""
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob_name = f"uploads/{document_id}/{original_filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_file_path)
        gcs_path = f"gs://{GCS_BUCKET_NAME}/{blob_name}"
        print(f"✅ Successfully uploaded to: {gcs_path}")
        return gcs_path
    except Exception as e:
        print(f"❌ Error uploading to GCS: {e}")
        return None

def log_to_bigquery(metadata, bq_client):
    """Logs metadata to BigQuery."""
    try:
        table_id = f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET}.{BIGQUERY_TABLE}"
        errors = bq_client.insert_rows_json(table_id, [metadata])
        if not errors:
            print("✅ Successfully logged metadata.")
            return True
        else:
            print(f"❌ BigQuery insert errors: {errors}")
            return False
    except Exception as e:
        print(f"❌ Error logging to BigQuery: {e}")
        return False

def extract_text_from_file(file_path, vision_client):
    """Extracts text from various file types."""
    _, extension = os.path.splitext(file_path)
    ext = extension.lower()
    text = ""
    try:
        if ext == ".txt":
            with open(file_path, 'r', encoding='utf-8') as f: text = f.read()
        elif ext == ".pdf":
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages: text += page.extract_text() or ""
        elif ext == ".docx":
            doc = docx.Document(file_path)
            for para in doc.paragraphs: text += para.text + "\n"
        elif ext in [".png", ".jpg", ".jpeg"]:
            if not vision_client: raise Exception("Vision client not available.")
            with open(file_path, 'rb') as image_file: content = image_file.read()
            image = vision.Image(content=content)
            response = vision_client.text_detection(image=image)
            if response.error.message: raise Exception(f"Vision API Error: {response.error.message}")
            text = response.text_annotations[0].description if response.text_annotations else ""
        else: raise ValueError(f"Unsupported file type: {ext}")
        print(f"✅ Successfully extracted {len(text)} characters.")
        return text
    except Exception as e:
        print(f"❌ An error occurred while extracting text: {e}")
        return None

# --- API ENDPOINTS ---

@app.route('/analyze', methods=['POST'])
def analyze_document():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    try:
        target_language = request.form['language']
        
        # Configure Gemini and Cloud clients using hardcoded values
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_client = genai.GenerativeModel('gemini-1.5-flash-latest')
        cloud_clients = initialize_clients()
        
        if not cloud_clients:
             return jsonify({"error": "Failed to initialize Google Cloud clients. Check backend logs and configuration."}), 500

    except Exception as e:
        return jsonify({"error": f"Configuration or Initialization Error: {e}"}), 500

    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        document_id = str(uuid.uuid4())
        
        gcs_path = upload_to_gcs(tmp_path, cloud_clients["storage"], document_id, file.filename)
        if not gcs_path:
            return jsonify({"error": "Failed to upload to Google Cloud Storage."}), 500

        metadata = {
            "document_id": document_id,
            "filename": file.filename,
            "file_type": os.path.splitext(file.filename)[1].lower(),
            "file_size": os.path.getsize(tmp_path),
            "upload_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "UPLOADED",
            "storage_path": gcs_path,
        }
        log_to_bigquery(metadata, cloud_clients["bigquery"])

        document_text = extract_text_from_file(tmp_path, cloud_clients["vision"])
        if document_text is None:
             return jsonify({"error": "Failed to extract text from the document."}), 500

        initial_prompt = f"""You are an expert Indian legal assistant. Analyze the user's document based on the provided legal knowledge base. Provide a structured breakdown in {target_language}. The output must strictly follow this format: ### Summary, ### Risk Analysis, ### Key Clauses & Legal Connections, ### Potential Mistakes & Ambiguities.

When generating the '### Key Clauses & Legal Connections' section, you MUST refer to the following legal texts to identify relevant clauses and articles. Cite the specific section or article number (e.g., BNS Section 101, Article 14 of the Indian Constitution).

--- LEGAL KNOWLEDGE BASE ---
{LEGAL_KNOWLEDGE_BASE}
--- END KNOWLEDGE BASE ---

--- USER'S DOCUMENT ---
{document_text}
--- END DOCUMENT ---
"""
        response = gemini_client.generate_content(initial_prompt)
        
        return jsonify({
            "analysis": response.text,
            "documentText": document_text
        })

    except Exception as e:
        print(f"Error during analysis: {e}")
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500
    finally:
        os.remove(tmp_path)

@app.route('/chat', methods=['POST'])
def chat_with_document():
    data = request.get_json()
    if not all(k in data for k in ['history', 'language']):
        return jsonify({"error": "Missing data for chat endpoint"}), 400

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        chat_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        chat = chat_model.start_chat(history=data['history'])
        
        user_question = data['history'][-1]['parts'][0]['text']
        response = chat.send_message(f"Based on the document context I provided, answer this question in {data['language']}: {user_question}")

        return jsonify({"response": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

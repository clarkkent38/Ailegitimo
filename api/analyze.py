from flask import Flask, request, jsonify
import os
import io
import json
import base64
import logging
import traceback
from datetime import datetime
import tempfile

# Google Cloud imports
from google.oauth2 import service_account
from google.cloud import storage
from google.cloud import bigquery
from google.cloud import vision
import google.generativeai as genai

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize Google Cloud clients
def get_credentials():
    """Get Google Cloud credentials from environment variable"""
    try:
        credentials_base64 = os.getenv('GCP_CREDENTIALS_BASE64')
        if credentials_base64:
            # Decode base64 credentials
            credentials_json = base64.b64decode(credentials_base64).decode('utf-8')
            credentials_dict = json.loads(credentials_json)
            return service_account.Credentials.from_service_account_info(credentials_dict)
        else:
            # Fallback for local development
            creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
            if creds_path and os.path.exists(creds_path):
                return service_account.Credentials.from_service_account_file(creds_path)
            else:
                raise ValueError("No valid credentials found")
    except Exception as e:
        logger.error(f"Credentials error: {str(e)}")
        raise

def initialize_clients():
    """Initialize Google Cloud clients"""
    try:
        credentials = get_credentials()
        project_id = os.getenv('GCP_PROJECT_ID')
        
        storage_client = storage.Client(credentials=credentials, project=project_id)
        bigquery_client = bigquery.Client(credentials=credentials, project=project_id)
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
        
        return storage_client, bigquery_client, vision_client
    except Exception as e:
        logger.error(f"Client initialization error: {str(e)}")
        raise

# Configure Gemini AI
def configure_gemini():
    """Configure Gemini AI"""
    try:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found")
        genai.configure(api_key=api_key)
        return genai.GenerativeModel('gemini-1.5-flash')
    except Exception as e:
        logger.error(f"Gemini configuration error: {str(e)}")
        raise

# Error handlers
@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 error: {str(error)}")
    return jsonify({
        'success': False,
        'error': 'Internal server error occurred'
    }), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'error': 'Endpoint not found'
    }), 404

@app.errorhandler(413)
def too_large(error):
    return jsonify({
        'success': False,
        'error': 'File too large. Maximum size is 10MB.'
    }), 413

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Check environment variables
        required_env_vars = ['GCP_PROJECT_ID', 'GEMINI_API_KEY', 'GCS_BUCKET_NAME']
        env_status = {}
        
        for var in required_env_vars:
            env_status[var] = bool(os.getenv(var))
        
        # Test credentials
        try:
            get_credentials()
            credentials_valid = True
        except:
            credentials_valid = False
        
        return jsonify({
            'status': 'healthy',
            'environment': 'vercel',
            'environment_variables': env_status,
            'credentials_valid': credentials_valid,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

def extract_text_from_file(file, vision_client):
    """Extract text from uploaded file"""
    try:
        file_content = file.read()
        file.seek(0)  # Reset file pointer
        
        filename = file.filename.lower()
        
        if filename.endswith(('.png', '.jpg', '.jpeg')):
            # Use Vision AI for images
            image = vision.Image(content=file_content)
            response = vision_client.text_detection(image=image)
            texts = response.text_annotations
            
            if texts:
                return texts[0].description
            else:
                return "No text found in image"
                
        elif filename.endswith('.txt'):
            # Handle text files
            try:
                return file_content.decode('utf-8')
            except UnicodeDecodeError:
                return file_content.decode('latin-1')
                
        elif filename.endswith('.pdf'):
            # For PDF files, you'd need to implement PDF text extraction
            # For now, return a message asking to convert to image
            return "PDF processing not implemented. Please convert to image format."
            
        elif filename.endswith('.docx'):
            # For DOCX files, you'd need python-docx library
            return "DOCX processing not implemented. Please convert to text or image format."
            
        else:
            return "Unsupported file format"
            
    except Exception as e:
        logger.error(f"Text extraction error: {str(e)}")
        raise

def upload_to_gcs(file, storage_client, bucket_name):
    """Upload file to Google Cloud Storage"""
    try:
        bucket = storage_client.bucket(bucket_name)
        
        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        blob_name = f"documents/{timestamp}_{file.filename}"
        
        blob = bucket.blob(blob_name)
        file.seek(0)
        blob.upload_from_file(file, content_type=file.content_type)
        
        logger.info(f"File uploaded to GCS: {blob_name}")
        return blob_name
        
    except Exception as e:
        logger.error(f"GCS upload error: {str(e)}")
        raise

def log_to_bigquery(document_data, bigquery_client):
    """Log document metadata to BigQuery"""
    try:
        dataset_id = os.getenv('BIGQUERY_DATASET')
        table_id = os.getenv('BIGQUERY_TABLE')
        
        if not dataset_id or not table_id:
            logger.warning("BigQuery dataset or table not configured")
            return
            
        table_ref = bigquery_client.dataset(dataset_id).table(table_id)
        table = bigquery_client.get_table(table_ref)
        
        rows_to_insert = [document_data]
        errors = bigquery_client.insert_rows_json(table, rows_to_insert)
        
        if errors:
            logger.error(f"BigQuery insert errors: {errors}")
        else:
            logger.info("Document logged to BigQuery successfully")
            
    except Exception as e:
        logger.error(f"BigQuery logging error: {str(e)}")
        # Don't raise - logging failure shouldn't stop the analysis

def analyze_with_gemini(text, gemini_model):
    """Analyze document text with Gemini AI"""
    try:
        # Load knowledge bases
        bns_knowledge = ""
        constitution_knowledge = ""
        
        try:
            with open('bns_knowledge_base.txt', 'r', encoding='utf-8') as f:
                bns_knowledge = f.read()
        except FileNotFoundError:
            logger.warning("BNS knowledge base file not found")
            
        try:
            with open('indian_constitution.txt', 'r', encoding='utf-8') as f:
                constitution_knowledge = f.read()
        except FileNotFoundError:
            logger.warning("Indian Constitution file not found")
        
        # Create analysis prompt
        prompt = f"""
        Analyze the following legal document and provide a comprehensive analysis:

        Document Text:
        {text[:4000]}  # Limit text to avoid token limits

        Please provide analysis in the following structured format:

        ## Summary
        [Brief overview of the document]

        ## Risk Analysis
        [Identify potential risks and liabilities]

        ## Key Clauses & Legal Connections
        [Important clauses and their connections to BNS and Indian Constitution]

        ## Potential Mistakes & Ambiguities
        [Unclear language or potential legal loopholes]

        ## Recommendations
        [Suggestions for improvement]

        Knowledge Base Context:
        BNS: {bns_knowledge[:2000] if bns_knowledge else "Not available"}
        Constitution: {constitution_knowledge[:2000] if constitution_knowledge else "Not available"}
        """
        
        response = gemini_model.generate_content(prompt)
        return response.text
        
    except Exception as e:
        logger.error(f"Gemini analysis error: {str(e)}")
        raise

@app.route('/api/analyze', methods=['POST'])
def analyze_document():
    """Main document analysis endpoint"""
    logger.info("=== Starting document analysis ===")
    
    try:
        # Validate file upload
        if 'document' not in request.files:
            logger.error("No document file in request")
            return jsonify({
                'success': False,
                'error': 'No document file provided'
            }), 400
        
        file = request.files['document']
        if file.filename == '':
            logger.error("No file selected")
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400
        
        # Check file size (10MB limit for Vercel)
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        
        if file_size > 10 * 1024 * 1024:  # 10MB
            return jsonify({
                'success': False,
                'error': 'File too large. Maximum size is 10MB.'
            }), 413
        
        logger.info(f"Processing file: {file.filename}, size: {file_size} bytes")
        
        # Initialize clients
        try:
            storage_client, bigquery_client, vision_client = initialize_clients()
            gemini_model = configure_gemini()
            logger.info("All clients initialized successfully")
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Service initialization failed: {str(e)}'
            }), 500
        
        # Extract text from file
        try:
            extracted_text = extract_text_from_file(file, vision_client)
            if not extracted_text or len(extracted_text.strip()) < 10:
                return jsonify({
                    'success': False,
                    'error': 'No meaningful text could be extracted from the document'
                }), 400
            logger.info(f"Text extracted successfully, length: {len(extracted_text)} characters")
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Text extraction failed: {str(e)}'
            }), 500
        
        # Upload to Google Cloud Storage
        try:
            bucket_name = os.getenv('GCS_BUCKET_NAME')
            if bucket_name:
                storage_path = upload_to_gcs(file, storage_client, bucket_name)
            else:
                storage_path = None
                logger.warning("GCS bucket not configured, skipping upload")
        except Exception as e:
            logger.error(f"GCS upload failed: {str(e)}")
            storage_path = None  # Continue without storage
        
        # Analyze with Gemini
        try:
            analysis = analyze_with_gemini(extracted_text, gemini_model)
            logger.info("Gemini analysis completed successfully")
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Analysis failed: {str(e)}'
            }), 500
        
        # Log to BigQuery
        try:
            document_data = {
                'document_id': f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}",
                'filename': file.filename,
                'upload_timestamp': datetime.now().isoformat(),
                'status': 'analyzed',
                'storage_path': storage_path
            }
            log_to_bigquery(document_data, bigquery_client)
        except Exception as e:
            logger.error(f"BigQuery logging failed: {str(e)}")
            # Continue without logging
        
        logger.info("=== Document analysis completed successfully ===")
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'extracted_text_length': len(extracted_text),
            'document_id': document_data.get('document_id')
        })
        
    except Exception as e:
        logger.error(f"Unexpected error in analyze_document: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        return jsonify({
            'success': False,
            'error': f'Unexpected server error: {str(e)}'
        }), 500

# Chat endpoint for follow-up questions
@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle follow-up questions about analyzed document"""
    try:
        data = request.get_json()
        if not data or 'question' not in data:
            return jsonify({
                'success': False,
                'error': 'No question provided'
            }), 400
        
        question = data['question']
        context = data.get('context', '')  # Previous analysis context
        
        gemini_model = configure_gemini()
        
        prompt = f"""
        Based on the previous legal document analysis, please answer the following question:
        
        Question: {question}
        
        Previous Analysis Context:
        {context[:3000] if context else "No previous context available"}
        
        Please provide a helpful and accurate response based on the document analysis.
        """
        
        response = gemini_model.generate_content(prompt)
        
        return jsonify({
            'success': True,
            'response': response.text
        })
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Chat failed: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(debug=False)

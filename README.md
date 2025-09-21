# Ailegitimo

**#AI Legitimo - Intelligent Legal Document Analyzer**
AI Legitimo is a secure, cloud-native web application that leverages Google's Generative AI (Gemini) and Google Cloud Platform to provide in-depth analysis of legal documents. Users can upload documents in various formats, receive a structured breakdown in multiple languages, and ask follow-up questions in an interactive chat. The analysis is grounded in specific legal texts, including the Bharatiya Nyaya Sanhita (BNS) and the Indian Constitution, for enhanced accuracy and relevance.

**✨ Features**
Secure Document Upload: Supports PDF, DOCX, TXT, and image files (PNG, JPG).

Cloud-Integrated Workflow: Automatically uploads documents to Google Cloud Storage for persistence and logs metadata in Google BigQuery for analytics.

Advanced Text Extraction: Utilizes Google's Vision AI for accurate text extraction from images and scans.

Multi-Language Analysis: Provides summaries and analysis in English, Hindi, and Kannada.

Structured AI Breakdown: Generates a clear, structured analysis including:

Summary: A concise overview of the document.

Risk Analysis: Identifies potential risks and liabilities.

Key Clauses & Legal Connections: Pinpoints important clauses and links them to specific sections of the BNS and the Indian Constitution.

Potential Mistakes & Ambiguities: Highlights unclear language or potential legal loopholes.

Interactive Q&A Chat: Allows users to ask follow-up questions about the document in a conversational interface.

Serverless Deployment: Optimized for modern, scalable, and cost-effective deployment on Vercel.

**🛠️ Technology Stack**
Frontend: HTML, Tailwind CSS, JavaScript

Backend: Python (Flask)

Cloud & AI:

Google Gemini 1.5 Flash

Google Cloud Storage

Google BigQuery

Google Cloud Vision AI

Deployment: Vercel

**🚀 Deployment Guide**
This project is designed for deployment on Vercel. Follow these steps to get your own instance running.

Prerequisites
A Google Cloud Platform (GCP) account with billing enabled.

A Vercel account.

A GitHub, GitLab, or Bitbucket account.

Python 3.8+ installed on your local machine.

**Step 1: Google Cloud Project Setup**
**Step 2: Create a GCP Project**: Go to the GCP Console and create a new project.

Enable APIs: In your new project, enable the following APIs:

Generative Language API

Cloud Storage API

BigQuery API

Cloud Vision AI API

**step 3: Create a Service Account:
**
Go to IAM & Admin > Service Accounts.

Click + CREATE SERVICE ACCOUNT.

Give it a name (e.g., ai-legitimo-service-account).

Grant the following roles: Storage Admin, BigQuery Data Editor, BigQuery Job User, and Cloud Vision AI User.

Click Done.

Find your new service account, click the three-dot menu under Actions, and select Manage keys.

Click ADD KEY > Create new key. Choose JSON and click CREATE. A JSON key file will be downloaded. Keep this file secure!

**4. Create a Cloud Storage Bucket:**

Go to Cloud Storage > Buckets.

Click + CREATE.

Give it a unique name and follow the on-screen instructions.

**5. Create a BigQuery Dataset and Table:**

Go to BigQuery > SQL Workspace.

Click the three-dot menu next to your project ID and select Create dataset. Give it a Dataset ID.

Select your new dataset, then click CREATE TABLE.

Set the Table name.

Under Schema, click Edit as text and paste the following JSON:

[
  {"name": "document_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "filename", "type": "STRING", "mode": "NULLABLE"},
  {"name": "upload_timestamp", "type": "TIMESTAMP", "mode": "NULLABLE"},
  {"name": "status", "type": "STRING", "mode": "NULLABLE"},
  {"name": "storage_path", "type": "STRING", "mode": "NULLABLE"}
]

Click CREATE TABLE.

**Step 2: Project Structure & Code Setup**
Clone the Repository: Clone your project code to your local machine.

Arrange Files: Ensure your project has the following structure:

/
├── api/
│   └── app.py
├── public/
│   └── index.html
├── bns_knowledge_base.txt
├── indian_constitution.txt
├── requirements.txt
└── vercel.json

**Step 3: Deploy to Vercel**
**1.Push to Git:** Push your project code to your preferred Git provider (e.g., GitHub).

**2. Create a Vercel Project:**

Log in to your Vercel account.

Click Add New... > Project.

Import the Git repository you just created.

**3. Configure Environment Variables:** Before the first deployment, go to the project's Settings > Environment Variables. Add the following secrets:

GEMINI_API_KEY: Your Gemini API key.

GCP_PROJECT_ID: Your Google Cloud Project ID.

GCS_BUCKET_NAME: The name of the Cloud Storage bucket you created.

BIGQUERY_DATASET: The ID of the BigQuery dataset you created.

BIGQUERY_TABLE: The name of the BigQuery table you created.

GCP_CREDENTIALS_JSON: Important! Open the service account JSON file you downloaded in Step 1. Copy the entire contents of the file and paste it into the value field in Vercel.

**4. Deploy:** Go to the Deployments tab and trigger a new deployment. Vercel will automatically install the Python dependencies and deploy your application.

Once the deployment is complete, you can access your live AI Legitimo application from the Vercel domain provided.

**📄 License**
This project is licensed under the MIT License. See the LICENSE file for details.

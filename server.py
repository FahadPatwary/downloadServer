import os
import threading
import time

import aria2p
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure Aria2
aria2 = aria2p.API(
    aria2p.Client(
        host="http://localhost",
        port=6800,
        secret=""
    )
)

# Google OAuth2 Configuration
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CLIENT_SECRETS_FILE = "client_secrets.json"
DOWNLOAD_DIR = "downloads"

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Store download progress
downloads = {}

def update_download_status(gid, status):
    """Send download status updates through WebSocket"""
    download = aria2.get_download(gid)
    if download.is_complete:
        status_data = {
            'status': 'completed',
            'progress': 100,
            'filePath': os.path.join(DOWNLOAD_DIR, download.files[0].path),
            'speed': 0,
            'downloaded': download.total_length,
            'total': download.total_length
        }
    else:
        status_data = {
            'status': status,
            'progress': download.progress,
            'speed': download.download_speed,
            'downloaded': download.completed_length,
            'total': download.total_length
        }
    
    socketio.emit('download_progress', status_data)

def monitor_download(gid):
    """Monitor download progress and send updates"""
    while True:
        try:
            download = aria2.get_download(gid)
            if download.is_complete:
                update_download_status(gid, 'completed')
                break
            elif download.has_failed:
                update_download_status(gid, 'failed')
                break
            else:
                update_download_status(gid, 'downloading')
            time.sleep(1)
        except Exception as e:
            print(f"Error monitoring download: {e}")
            break

@app.route('/download', methods=['POST'])
def start_download():
    try:
        data = request.json
        url = data.get('url')
        save_to_cloud = data.get('saveToCloud', False)

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        # Start download with aria2
        download = aria2.add_uris([url], {'dir': DOWNLOAD_DIR})
        
        # Start monitoring thread
        threading.Thread(target=monitor_download, args=(download.gid,), daemon=True).start()

        return jsonify({
            'message': 'Download started',
            'gid': download.gid
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/auth/google')
def google_auth():
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri='http://localhost:5000/oauth2callback'
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )

        return jsonify({'authUrl': authorization_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/oauth2callback')
def oauth2callback():
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri='http://localhost:5000/oauth2callback'
        )
        
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        # Save credentials for future use
        with open('token.json', 'w') as token:
            token.write(credentials.to_json())

        return "Authentication successful! You can close this window."
    except Exception as e:
        return f"Error: {str(e)}", 500

def upload_to_drive(file_path):
    """Upload file to Google Drive"""
    try:
        with open('token.json', 'r') as token:
            credentials = Credentials.from_authorized_user_file('token.json', SCOPES)
        
        service = build('drive', 'v3', credentials=credentials)
        
        file_metadata = {'name': os.path.basename(file_path)}
        media = MediaFileUpload(file_path, resumable=True)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        return file.get('id')
    except Exception as e:
        print(f"Error uploading to Drive: {e}")
        return None

if __name__ == '__main__':
    socketio.run(app, debug=True) 
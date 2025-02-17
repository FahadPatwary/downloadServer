import json
import os
import threading
import time
from urllib.parse import urlparse

import aria2p
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["*"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Configure Aria2 with environment variables or defaults
ARIA2_HOST = os.getenv('ARIA2_HOST', 'http://localhost')
ARIA2_PORT = int(os.getenv('ARIA2_PORT', 6800))
ARIA2_SECRET = os.getenv('ARIA2_SECRET', '')

try:
    aria2 = aria2p.API(
        aria2p.Client(
            host=ARIA2_HOST,
            port=ARIA2_PORT,
            secret=ARIA2_SECRET
        )
    )
except Exception as e:
    print(f"Error initializing Aria2: {e}")
    # Initialize a mock aria2 for testing
    class MockAria2:
        def add_uris(self, uris, options):
            return MockDownload()
        
        def get_download(self, gid):
            return MockDownload()
    
    class MockDownload:
        def __init__(self):
            self.gid = "mock_gid"
            self.is_complete = False
            self.has_failed = False
            self.progress = 0
            self.download_speed = 1024 * 1024  # 1MB/s
            self.completed_length = 0
            self.total_length = 1024 * 1024 * 100  # 100MB
            self.files = [type('obj', (), {'path': 'mock_file.mp4'})]
    
    aria2 = MockAria2()

# Google OAuth2 Configuration
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CLIENT_SECRETS_FILE = os.getenv('CLIENT_SECRETS_FILE', 'client_secrets.json')
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', '/tmp/downloads')

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Store download progress
downloads = {}

def update_download_status(gid, status):
    """Send download status updates through WebSocket"""
    try:
        download = aria2.get_download(gid)
        if download.is_complete:
            file_path = os.path.join(DOWNLOAD_DIR, download.files[0].path)
            status_data = {
                'status': 'completed',
                'progress': 100,
                'filePath': file_path,
                'fileUrl': f'/downloads/{os.path.basename(file_path)}',
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
    except Exception as e:
        print(f"Error updating download status: {e}")
        socketio.emit('download_progress', {
            'status': 'error',
            'error': str(e)
        })

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
            socketio.emit('download_progress', {
                'status': 'error',
                'error': str(e)
            })
            break

@app.route('/downloads/<path:filename>')
def download_file(filename):
    """Serve downloaded files"""
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route('/download', methods=['POST', 'OPTIONS'])
def start_download():
    if request.method == 'OPTIONS':
        return '', 204
        
    try:
        data = request.json
        url = data.get('url')
        save_to_cloud = data.get('saveToCloud', False)

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        # Validate URL
        try:
            parsed_url = urlparse(url)
            if not parsed_url.scheme or not parsed_url.netloc:
                return jsonify({'error': 'Invalid URL format'}), 400
        except Exception:
            return jsonify({'error': 'Invalid URL format'}), 400

        # Start download with aria2
        download = aria2.add_uris([url], {
            'dir': DOWNLOAD_DIR,
            'max-connection-per-server': '16',
            'split': '16'
        })
        
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
        if not os.path.exists(CLIENT_SECRETS_FILE):
            return jsonify({'error': 'Google Drive integration not configured'}), 503
            
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=f'{request.url_root}oauth2callback'
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
            redirect_uri=f'{request.url_root}oauth2callback'
        )
        
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        # Save credentials for future use
        with open('token.json', 'w') as token:
            token.write(credentials.to_json())

        return "Authentication successful! You can close this window."
    except Exception as e:
        return f"Error: {str(e)}", 500

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    # Get port from environment variable (Glitch uses process.env.PORT)
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True) 
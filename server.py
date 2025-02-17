import json
import mimetypes
import os
import threading
import time
from urllib.parse import unquote, urlparse

import aria2p
from flask import Flask, jsonify, request, send_file, send_from_directory
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
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', '/tmp/downloads')

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Configure allowed file types
ALLOWED_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.webm',
    '.flv', '.wmv', '.m4v', '.mpg', '.mpeg'
}

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

def is_valid_file_type(filename):
    """Check if the file type is allowed"""
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS

def get_safe_filename(filename):
    """Generate a safe filename"""
    filename = unquote(filename)
    return "".join([c for c in filename if c.isalpha() or c.isdigit() or c in (' ', '-', '_', '.')]).rstrip()

def update_download_status(gid, status):
    """Send download status updates through WebSocket"""
    try:
        download = aria2.get_download(gid)
        if download.is_complete:
            filename = os.path.basename(download.files[0].path)
            safe_filename = get_safe_filename(filename)
            file_path = os.path.join(DOWNLOAD_DIR, safe_filename)
            
            # Move file to safe filename if needed
            if os.path.exists(download.files[0].path) and download.files[0].path != file_path:
                os.rename(download.files[0].path, file_path)
            
            status_data = {
                'status': 'completed',
                'progress': 100,
                'filePath': file_path,
                'fileUrl': f'/downloads/{safe_filename}',
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
    try:
        if not is_valid_file_type(filename):
            return jsonify({'error': 'Invalid file type'}), 400

        safe_filename = get_safe_filename(filename)
        file_path = os.path.join(DOWNLOAD_DIR, safe_filename)
        
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404

        # Get the file's mime type
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        return send_file(
            file_path,
            mimetype=mime_type,
            as_attachment=False,
            download_name=safe_filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

        # Check file extension from URL
        filename = os.path.basename(parsed_url.path)
        if not is_valid_file_type(filename):
            return jsonify({'error': 'Invalid file type. Only video files are allowed.'}), 400

        # Start download with aria2
        download = aria2.add_uris([url], {
            'dir': DOWNLOAD_DIR,
            'max-connection-per-server': '16',
            'split': '16',
            'out': get_safe_filename(filename)
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

import json
import mimetypes
import os
import threading
import time
from datetime import datetime
from urllib.parse import unquote, urlparse

import requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_socketio import SocketIO

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": ["*"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Configure download directory
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', '/tmp/downloads')

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Configure allowed file types
ALLOWED_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.webm',
    '.flv', '.wmv', '.m4v', '.mpg', '.mpeg'
}

# Store active downloads
active_downloads = {}

class DownloadManager:
    def __init__(self, url, filename):
        self.url = url
        self.filename = filename
        self.file_path = os.path.join(DOWNLOAD_DIR, filename)
        self.total_size = 0
        self.downloaded_size = 0
        self.speed = 0
        self.status = 'initializing'
        self.is_complete = False
        self.has_failed = False
        self.start_time = None
        self.last_update_time = None
        self.last_downloaded_size = 0

    def calculate_speed(self):
        if not self.last_update_time:
            return 0
        
        current_time = time.time()
        time_diff = current_time - self.last_update_time
        if time_diff == 0:
            return self.speed

        size_diff = self.downloaded_size - self.last_downloaded_size
        speed = size_diff / time_diff

        self.last_update_time = current_time
        self.last_downloaded_size = self.downloaded_size
        self.speed = speed
        return speed

    def get_progress(self):
        if self.total_size == 0:
            return 0
        return (self.downloaded_size / self.total_size) * 100

    def download(self):
        try:
            self.start_time = time.time()
            self.last_update_time = self.start_time
            self.status = 'downloading'

            response = requests.get(self.url, stream=True)
            response.raise_for_status()
            
            self.total_size = int(response.headers.get('content-length', 0))
            
            with open(self.file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        self.downloaded_size += len(chunk)
                        self.calculate_speed()
                        self._emit_status()

            self.is_complete = True
            self.status = 'completed'
            self._emit_status()
            
        except Exception as e:
            self.has_failed = True
            self.status = f'failed: {str(e)}'
            self._emit_status()
            raise e

    def _emit_status(self):
        status_data = {
            'status': self.status,
            'progress': self.get_progress(),
            'speed': self.speed,
            'downloaded': self.downloaded_size,
            'total': self.total_size
        }

        if self.is_complete:
            status_data.update({
                'filePath': self.file_path,
                'fileUrl': f'/downloads/{self.filename}'
            })

        socketio.emit('download_progress', status_data)

def is_valid_file_type(filename):
    """Check if the file type is allowed"""
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS

def get_safe_filename(filename):
    """Generate a safe filename"""
    filename = unquote(filename)
    base = "".join([c for c in filename if c.isalpha() or c.isdigit() or c in (' ', '-', '_', '.')]).rstrip()
    name, ext = os.path.splitext(base)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"{name}_{timestamp}{ext}"

@app.route('/downloads/<path:filename>')
def download_file(filename):
    """Serve downloaded files"""
    try:
        if not is_valid_file_type(filename):
            return jsonify({'error': 'Invalid file type'}), 400

        file_path = os.path.join(DOWNLOAD_DIR, filename)
        
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
            download_name=filename
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

        # Generate safe filename
        safe_filename = get_safe_filename(filename)
        
        # Create download manager
        download_manager = DownloadManager(url, safe_filename)
        active_downloads[safe_filename] = download_manager
        
        # Start download in a separate thread
        thread = threading.Thread(
            target=download_manager.download,
            daemon=True
        )
        thread.start()

        return jsonify({
            'message': 'Download started',
            'filename': safe_filename
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True) 
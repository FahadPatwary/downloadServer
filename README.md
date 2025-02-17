# Video Download Server

A Flask-based server that handles video file downloads with real-time progress tracking via WebSocket.

## Features

- Real-time download progress tracking
- WebSocket support for live updates
- Secure file handling
- Support for various video formats
- Automatic file cleanup
- Railway.app deployment ready

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run the server locally:

```bash
python server.py
```

## Environment Variables

- `PORT`: Server port (default: 5000)
- `DOWNLOAD_DIR`: Directory for downloaded files (default: /tmp/downloads)

## Deployment to Railway

1. Create a new project on Railway.app
2. Connect your GitHub repository
3. Set the following:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python -m gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 --bind 0.0.0.0:$PORT server:app`

## API Endpoints

### POST /download

Start a file download

```json
{
  "url": "https://example.com/video.mp4"
}
```

### GET /downloads/<filename>

Retrieve a downloaded file

### WebSocket Events

- `download_progress`: Real-time download progress updates

```json
{
  "status": "downloading",
  "progress": 45.5,
  "speed": 1048576,
  "downloaded": 1048576,
  "total": 2097152
}
```

## Supported File Types

- MP4 (.mp4)
- MKV (.mkv)
- AVI (.avi)
- MOV (.mov)
- WebM (.webm)
- FLV (.flv)
- WMV (.wmv)
- M4V (.m4v)
- MPEG (.mpg, .mpeg)

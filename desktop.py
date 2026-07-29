import threading
import time
import uvicorn
import webview
import socket
from app import app, logger

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def run_server(port):
    # Run FastAPI app on the specified port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

if __name__ == "__main__":
    # Use a fixed port to ensure the origin remains the same across sessions.
    # This is critical for localStorage persistence in the browser/webview.
    port = 5231

    # Start the server in a daemon thread so it dies when the window closes
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    # Give the server a moment to start
    time.sleep(1.0)

    # Create the native desktop window pointing to our local server
    window = webview.create_window(
        'WSJT-X Operator Console', 
        f'http://127.0.0.1:{port}',
        width=1200, 
        height=800,
        min_size=(800, 600)
    )
    
    import os
    storage_dir = os.path.join(os.getenv('LOCALAPPDATA', ''), 'WSJTX-Operator-Console', 'webview_storage')
    os.makedirs(storage_dir, exist_ok=True)
    
    # Start the webview GUI loop
    webview.start(private_mode=False, storage_path=storage_dir)

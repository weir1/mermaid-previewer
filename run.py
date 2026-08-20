import http.server
import socketserver
import webbrowser
import threading
import os
import socket

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
URL = f"http://localhost:{PORT}/index.html"

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

def start_server():
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving at {URL}")
        httpd.serve_forever()

if __name__ == "__main__":
    if is_port_in_use(PORT):
        print(f"Port {PORT} is already in use. Opening browser directly.")
        webbrowser.open(URL)
    else:
        # Start server in a background thread
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()

        # Open the browser
        print(f"Opening {URL} in your default browser...")
        webbrowser.open(URL)

        try:
            print("Press Ctrl+C to stop the server.")
            server_thread.join()
        except KeyboardInterrupt:
            print("\nStopping server...")

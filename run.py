import http.server
import socketserver
import webbrowser
import threading
import os

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

def start_server():
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    # Start server in a background thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Open the browser
    url = f"http://localhost:{PORT}/index.html"
    print(f"Opening {url} in your default browser...")
    webbrowser.open(url)

    try:
        print("Press Ctrl+C to stop the server.")
        server_thread.join()
    except KeyboardInterrupt:
        print("\nStopping server...")

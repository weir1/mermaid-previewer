# Mermaid Previewer 🧜‍♀️

A lightning-fast, zero-setup local tool for instantly previewing [Mermaid](https://mermaid.js.org/) diagrams on your Mac. Built to solve the pain of squished, unreadable diagrams in typical split-pane web editors, this tool gives you a distraction-free, full-screen canvas with native pan, zoom, and desktop-app support.

## ✨ Features

- **Live Editor & Full-Screen Canvas:** Type or paste your Mermaid code in the editor, then hit `Cmd + Enter` to instantly view it on a massive, edge-to-edge canvas.
- **Native Pan & Zoom:** Scroll up/down/left/right using your trackpad, or pinch-to-zoom for massive diagrams.
- **Keyboard Navigation:** Navigate large architectures effortlessly using Arrow Keys.
- **Drag and Drop:** Grab any `.mmd` or `.txt` file and drop it into the editor to load the code.
- **Mac Desktop App Integration:** Includes a Python background daemon and an AppleScript `MermaidPreviewer.app` so you can launch the environment silently from your Desktop without ever seeing a terminal window.

## 🚀 Getting Started

### Prerequisites
- Python 3 installed on your machine.

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/weir1/mermaid-previewer.git
   cd mermaid-previewer
   ```
2. Start the local server:
   ```bash
   python3 run.py
   ```
   This will automatically spin up a lightweight background server and open `http://localhost:8000/index.html` in your default browser.

### Desktop Shortcut (Mac Only)
If you want to use the app without touching the terminal, you can generate a desktop shortcut:
```bash
osacompile -e 'do shell script "cd '`pwd`' && nohup /usr/bin/python3 run.py >/dev/null 2>&1 &"' -o ~/Desktop/MermaidPreviewer.app
```
Double-click the new `MermaidPreviewer.app` on your desktop anytime you want to launch the tool!

## ⌨️ Shortcuts & Cheatsheet

| Shortcut / Action | What it does |
| :--- | :--- |
| `Cmd + Enter` | Show the full-screen preview |
| `F` | Toggle browser Native Fullscreen (while in preview) |
| `Esc` | Close the preview overlay |
| **Arrow Keys** | Pan the diagram Left, Right, Up, or Down |
| **Two-finger Scroll** | Pan around the diagram smoothly |
| **Pinch** / `Cmd + Scroll` | Zoom in and out |

## 🛠 Tech Stack

- Vanilla HTML/JS/CSS (No heavy build tools or Webpack)
- [`mermaid.js`](https://mermaid.js.org/) (ES Module via CDN)
- [`svg-pan-zoom`](https://github.com/bumbu/svg-pan-zoom) (For fluid navigation)
- Python 3 `http.server` (For local hosting and avoiding CORS)

## 🤝 Contributing
Feel free to open an issue or submit a PR if you have ideas on how to improve the previewer!

## 📄 License
This project is open-source and available under the MIT License.

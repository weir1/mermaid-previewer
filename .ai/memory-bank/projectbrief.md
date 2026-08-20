# Mermaid Previewer

A simple local tool to instantly preview Mermaid diagrams on a Mac.

## Core Purpose
Provide a zero-setup, immediate live-preview environment for Mermaid diagram code. The user can paste Mermaid code and see the visual representation update in real-time.

## Architecture
- `index.html`: A static HTML page with a split-pane layout containing an editor (`<textarea>`) and a live rendering pane using the `mermaid.esm.min.mjs` library.
- `run.py`: A Python script that spins up a local HTTP server and automatically opens the user's default browser to the previewer tool.

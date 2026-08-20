import os
import glob
from datetime import datetime

INBOX_DIR = os.path.expanduser("~/Documents/Notes/Telegram/Received_Notes")

def main():
    if not os.path.exists(INBOX_DIR):
        print(f"❌ Global inbox not found at {INBOX_DIR}")
        return

    notes = glob.glob(os.path.join(INBOX_DIR, "*.md"))
    if not notes:
        print("📭 Global inbox is empty.")
        return

    # Sort by modification time (newest first)
    notes.sort(key=os.path.getmtime, reverse=True)

    print(f"📬 Found {len(notes)} unprocessed notes in the Global Telegram Inbox:\n")
    print("These notes were not routed because they lacked a workspace tag (e.g. #subliminal).\n")

    for note in notes[:10]: # show top 10
        print(f"{"-"*15} {os.path.basename(note)} {"-"*15}")
        with open(note, 'r') as f:
            print(f.read().strip())
        print("\n")

    if len(notes) > 10:
        print(f"... and {len(notes) - 10} more notes.")

if __name__ == "__main__":
    main()

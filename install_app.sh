#!/bin/bash
APP_NAME="MermaidPreviewer.app"
DEST="$HOME/Applications/$APP_NAME"
DIR="$(pwd)"

echo "Installing $APP_NAME to $DEST..."
mkdir -p "$DEST/Contents/MacOS"
cat << 'APP_EOF' > "$DEST/Contents/MacOS/MermaidPreviewer"
#!/bin/bash
# Automatically runs from the correct directory
DIR="APP_DIR_PLACEHOLDER"
cd "$DIR"
nohup /usr/bin/python3 run.py >/dev/null 2>&1 &
APP_EOF

sed -i '' "s|APP_DIR_PLACEHOLDER|$DIR|g" "$DEST/Contents/MacOS/MermaidPreviewer"
chmod +x "$DEST/Contents/MacOS/MermaidPreviewer"

cat << 'PLIST_EOF' > "$DEST/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>MermaidPreviewer</string>
    <key>CFBundleIdentifier</key>
    <string>com.moind.mermaidpreviewer</string>
    <key>CFBundleName</key>
    <string>MermaidPreviewer</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST_EOF

mdimport "$DEST"
echo "Done! You can now launch MermaidPreviewer from Spotlight (Cmd+Space)."

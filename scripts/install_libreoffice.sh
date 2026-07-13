#!/usr/bin/env bash
# Installs LibreOffice on macOS if not already present.
# Downloads the official DMG from libreoffice.org, mounts it, copies the
# .app bundle to /Applications, then unmounts.
#
# Re-running this script is safe: it skips the download if LibreOffice is
# already installed at /Applications/LibreOffice.app.
#
# Usage:
#   ./scripts/install_libreoffice.sh
#
set -euo pipefail

LO_APP="/Applications/LibreOffice.app"
SOFFICE_BIN="$LO_APP/Contents/MacOS/soffice"

if [ -x "$SOFFICE_BIN" ]; then
    echo "LibreOffice already installed at $LO_APP"
    echo "  binary: $SOFFICE_BIN"
    "$SOFFICE_BIN" --version | head -1 || true
    exit 0
fi

# Detect architecture to pick the right DMG.
ARCH=$(uname -m)
case "$ARCH" in
    arm64)   PLATFORM="aarch64" ;;
    x86_64)  PLATFORM="x86-64"   ;;
    *)
        echo "Unsupported architecture: $ARCH" >&2
        exit 1
        ;;
esac

# Resolve the latest stable DMG URL. LibreOffice publishes a curl-friendly
# redirect at https://download.documentfoundation.org/libreoffice/stable/
# We pin to a known major (25.x) for reproducibility; bump as needed.
echo "Resolving latest LibreOffice DMG for $PLATFORM …"
LATEST_DIR=$(curl -fsSL "https://download.documentfoundation.org/libreoffice/stable/" \
    | grep -oE 'href="[0-9]+\.[0-9]+\.[0-9]+/"' \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
    | sort -t. -k1,1n -k2,2n -k3,3n \
    | tail -1)
if [ -z "$LATEST_DIR" ]; then
    echo "Could not discover latest LibreOffice version from download.documentfoundation.org" >&2
    exit 1
fi
echo "Latest found: $LATEST_DIR"

DMG_NAME="LibreOffice_${LATEST_DIR}_MacOS_${PLATFORM}.dmg"
DMG_URL="https://download.documentfoundation.org/libreoffice/stable/${LATEST_DIR}/mac/${PLATFORM}/${DMG_NAME}"

TMP_DIR="$(mktemp -d)"
DMG_PATH="$TMP_DIR/$DMG_NAME"
echo "Downloading $DMG_URL"
curl -fSL --retry 3 -o "$DMG_PATH" "$DMG_URL"

echo "Mounting DMG…"
MOUNT_POINT=$(hdiutil attach "$DMG_PATH" -nobrowse -quiet | tail -1 | awk '{print $NF}')

APP_SRC="$(find "$MOUNT_POINT" -maxdepth 1 -name 'LibreOffice.app' -print -quit)"
if [ -z "$APP_SRC" ]; then
    echo "LibreOffice.app not found inside DMG" >&2
    hdiutil detach "$MOUNT_POINT" -quiet || true
    exit 1
fi

echo "Copying LibreOffice.app to /Applications …"
# ditto preserves extended attributes and resource forks.
sudo ditto "$APP_SRC" "$LO_APP" 2>/dev/null || ditto "$APP_SRC" "$LO_APP"

echo "Unmounting DMG…"
hdiutil detach "$MOUNT_POINT" -quiet || true

echo "Cleaning up…"
rm -f "$DMG_PATH"
rmdir "$TMP_DIR" 2>/dev/null || true

if [ -x "$SOFFICE_BIN" ]; then
    echo "OK: LibreOffice installed."
    "$SOFFICE_BIN" --version | head -1
    echo ""
    echo "Add this line to your .env (auto-tailored-cv):"
    echo "  SOFFICE_PATH=$SOFFICE_BIN"
else
    echo "Install finished but soffice binary not found at $SOFFICE_BIN" >&2
    exit 1
fi
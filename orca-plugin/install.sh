#!/bin/sh
# Install the Support Fins plugin into an OrcaSlicer data directory.
#   ./install.sh [datadir]      default: ~/.config/OrcaSlicer
set -eu
here=$(cd "$(dirname "$0")" && pwd)
datadir=${1:-"$HOME/.config/OrcaSlicer"}
dest="$datadir/orca_plugins/support_fins"
mkdir -p "$dest"
cp "$here/support_fins.py" "$dest/"
rm -rf "$dest/web"
cp -r "$here/../web" "$dest/web"
# Orca only lists a side-loaded plugin that has an install record beside it.
[ -f "$dest/.install_state.json" ] || cat > "$dest/.install_state.json" <<JSON
{
  "capabilities": [{ "Support Fins": true }],
  "enabled": true,
  "installed_from": "local",
  "installed_version": "0.1.0",
  "plugin_name": "Support Fins"
}
JSON
echo "Installed to $dest -- restart OrcaSlicer, then Plugins > Support Fins."

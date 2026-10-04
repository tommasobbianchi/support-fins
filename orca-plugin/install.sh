#!/bin/sh
# Install the Support Fins plugin into an OrcaSlicer data directory.
#   ./install.sh [datadir]      default: ~/.config/OrcaSlicer
set -eu
here=$(cd "$(dirname "$0")" && pwd)
datadir=${1:-"$HOME/.config/OrcaSlicer"}
dest="$datadir/orca_plugins/support_fins"
mkdir -p "$dest/build"
# The plugin opens a composed, self-contained page (no loopback server): build it
# from web/ + the bundles if it is not there yet, and install it beside the plugin.
[ -f "$here/build/panel.html" ] || python3 "$here/build_web.py"
cp "$here/support_fins.py" "$dest/"
cp "$here/build/panel.html" "$dest/build/"
rm -rf "$dest/web"          # the loopback server is gone; drop any old web/ copy
# Orca only lists a side-loaded plugin that has an install record beside it.
[ -f "$dest/.install_state.json" ] || cat > "$dest/.install_state.json" <<JSON
{
  "capabilities": [{ "Support Fins": true }],
  "enabled": true,
  "installed_from": "local",
  "installed_version": "0.2.0",
  "plugin_name": "Support Fins"
}
JSON
echo "Installed to $dest -- restart OrcaSlicer, then Plugins > Support Fins."

#!/bin/bash
# Install mx4ctl for the current user: CLI symlink, config, systemd user service.
set -euo pipefail
cd "$(dirname "$0")"

mkdir -p ~/.local/bin ~/.config/mx4ctl ~/.config/systemd/user
ln -sf "$PWD/mx4ctl" ~/.local/bin/mx4ctl
[ -f ~/.config/mx4ctl/config.ini ] || cp config.example.ini ~/.config/mx4ctl/config.ini
cp mx4ctl.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mx4ctl.service

echo "installed. status: systemctl --user status mx4ctl"
echo "config:    ~/.config/mx4ctl/config.ini (restart with: systemctl --user restart mx4ctl)"

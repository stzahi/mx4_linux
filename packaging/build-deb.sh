#!/bin/bash
# Build dist/mx4ctl_<version>_all.deb — a clean, apt-removable package.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import mx4; print(mx4.__version__)")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# -- payload -------------------------------------------------------------
install -d "$STAGE/usr/lib/mx4ctl/mx4" "$STAGE/usr/bin" \
           "$STAGE/usr/lib/systemd/user" "$STAGE/usr/lib/udev/rules.d" \
           "$STAGE/usr/share/mx4ctl" "$STAGE/usr/share/doc/mx4ctl"

install -m 644 mx4/*.py "$STAGE/usr/lib/mx4ctl/mx4/"
install -m 755 mx4ctl "$STAGE/usr/bin/mx4ctl"
install -m 644 config.example.ini "$STAGE/usr/share/mx4ctl/"
install -m 644 README.md "$STAGE/usr/share/doc/mx4ctl/"

cat > "$STAGE/usr/lib/systemd/user/mx4ctl.service" <<'EOF'
[Unit]
Description=MX Master 4 haptics & extras daemon (mx4ctl)
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/mx4ctl daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF

# hidraw access for logged-in users (USB receiver and Bluetooth), and
# uinput for the virtual keyboard that backs @built-in actions on Wayland
cat > "$STAGE/usr/lib/udev/rules.d/42-mx4ctl.rules" <<'EOF'
ACTION=="add", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", MODE="0660", TAG+="uaccess"
ACTION=="add", SUBSYSTEM=="hidraw", KERNELS=="0005:046D:*", MODE="0660", TAG+="uaccess"
KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", TAG+="uaccess", OPTIONS+="static_node=uinput"
EOF

# -- control files ----------------------------------------------------------
install -d "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: mx4ctl
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, python3-dbus
Recommends: xdotool, x11-utils, libnotify-bin
Maintainer: Giuseppe Festa <giuseppe.festa@neura-robotics.com>
Description: Logitech MX Master 4 haptics & extras for Linux
 Talks HID++ 2.0 directly to the MX Master 4: haptic waveforms,
 notification buzz, mouse gestures, thumb-button actions menu,
 DPI and SmartShift control, battery warnings. CLI plus a per-user
 daemon (enabled automatically for graphical sessions).
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
    systemctl --global enable mx4ctl.service 2>/dev/null || true
fi
EOF

cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
    systemctl --global disable mx4ctl.service 2>/dev/null || true
fi
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
udevadm control --reload-rules 2>/dev/null || true
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"

# -- build ----------------------------------------------------------------
mkdir -p dist
dpkg-deb --build --root-owner-group "$STAGE" "dist/mx4ctl_${VERSION}_all.deb"
echo
dpkg-deb --info "dist/mx4ctl_${VERSION}_all.deb" | sed -n '2,12p'
echo "→ dist/mx4ctl_${VERSION}_all.deb"

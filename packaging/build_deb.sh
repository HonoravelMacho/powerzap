#!/usr/bin/env bash
# Monta o pacote .deb a partir dos binários gerados pelo PyInstaller.
set -euo pipefail

VERSION="${1:-0.1.0}"
ARCH="$(dpkg --print-architecture)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/dist/debroot/powerzap_${VERSION}_${ARCH}"

rm -rf "$ROOT/dist/debroot"
mkdir -p \
  "$STAGE/DEBIAN" \
  "$STAGE/usr/bin" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/icons/hicolor/256x256/apps" \
  "$STAGE/usr/lib/systemd/user"

# Localiza binários (flet pack pode gerar em subpasta dist/powerzap/powerzap).
GUI_BIN="$ROOT/dist/powerzap"
if [ ! -f "$GUI_BIN" ] && [ -f "$ROOT/dist/powerzap/powerzap" ]; then
  GUI_BIN="$ROOT/dist/powerzap/powerzap"
fi
SCHED_BIN="$ROOT/dist/powerzap-scheduler"
if [ ! -f "$GUI_BIN" ]; then
  echo "ERRO: binário GUI não encontrado em dist/powerzap" >&2
  ls -R "$ROOT/dist" || true
  exit 1
fi
if [ ! -f "$SCHED_BIN" ]; then
  echo "ERRO: binário scheduler não encontrado em dist/powerzap-scheduler" >&2
  ls -R "$ROOT/dist" || true
  exit 1
fi

install -m 755 "$GUI_BIN" "$STAGE/usr/bin/powerzap"
install -m 755 "$SCHED_BIN" "$STAGE/usr/bin/powerzap-scheduler"
install -m 644 "$ROOT/packaging/powerzap.desktop" "$STAGE/usr/share/applications/"
install -m 644 "$ROOT/assets/icon.png" "$STAGE/usr/share/icons/hicolor/256x256/apps/powerzap.png"
install -m 644 "$ROOT/packaging/powerzap-scheduler.service" "$STAGE/usr/lib/systemd/user/"

INSTALLED_SIZE=$(( $(du -sk "$GUI_BIN" | cut -f1) + $(du -sk "$SCHED_BIN" | cut -f1) ))

cat > "$STAGE/DEBIAN/control" <<EOF
Package: powerzap
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Depends: libgl1, libglib2.0-0, libgtk-3-0, libblkid1, liblzma5, libmpv1 | libmpv2, systemd
Installed-Size: ${INSTALLED_SIZE}
Maintainer: HonoravelMacho <honoravelmacho@users.noreply.github.com>
Description: Agendador de mensagens WhatsApp com Evolution API
 PowerZap permite agendar e enviar mensagens do WhatsApp via
 Evolution API local, com calendário interativo, etiquetas
 coloridas e serviço de envio em background.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/bash
set -e
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
for user_home in /home/*; do
  user="$(basename "$user_home")"
  if id "$user" >/dev/null 2>&1; then
    sudo -u "$user" XDG_RUNTIME_DIR="/run/user/$(id -u $user)" \
      systemctl --user daemon-reload >/dev/null 2>&1 || true
    sudo -u "$user" XDG_RUNTIME_DIR="/run/user/$(id -u $user)" \
      systemctl --user enable --now powerzap-scheduler.service >/dev/null 2>&1 || true
  fi
done
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/bash
set -e
for user_home in /home/*; do
  user="$(basename "$user_home")"
  if id "$user" >/dev/null 2>&1; then
    sudo -u "$user" XDG_RUNTIME_DIR="/run/user/$(id -u $user)" \
      systemctl --user stop powerzap-scheduler.service >/dev/null 2>&1 || true
    sudo -u "$user" XDG_RUNTIME_DIR="/run/user/$(id -u $user)" \
      systemctl --user disable powerzap-scheduler.service >/dev/null 2>&1 || true
  fi
done
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/prerm"

dpkg-deb --build --root-owner-group "$STAGE" "$ROOT/dist/powerzap_${VERSION}_${ARCH}.deb"
echo "Pacote gerado: dist/powerzap_${VERSION}_${ARCH}.deb"

#!/usr/bin/env bash
set -euo pipefail
binary="${1:?usage: install.sh /path/frps /path/prepared-credentials EXPECTED_BINARY_SHA256}"
credentials="${2:?prepared credentials directory required}"
expected="${3:?trusted binary SHA-256 required}"
[[ "$EUID" -eq 0 ]] || { echo 'Run as root'; exit 1; }
[[ "$expected" =~ ^[a-fA-F0-9]{64}$ ]] || { echo 'Invalid expected hash'; exit 1; }
actual="$(sha256sum -- "$binary" | cut -d ' ' -f 1)"
[[ "${actual,,}" == "${expected,,}" ]] || { echo 'Binary hash mismatch'; exit 1; }
if ! "$binary" --version | head -n 1 | grep -Fxq '0.68.1'; then echo 'Review compatibility before using another FRP version'; exit 1; fi
for file in frps.toml tls.crt tls.key; do [[ -f "$credentials/$file" ]] || { echo "Missing $file"; exit 1; }; done
getent passwd chatbridge-relay >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin chatbridge-relay
install -d -m 0755 /usr/local/lib/chatbridge
install -d -o root -g chatbridge-relay -m 0750 /etc/chatbridge-relay
if [[ -e /etc/chatbridge-relay/frps.toml ]]; then
  echo 'Existing relay preserved. Stop the service, back up credentials and use the documented update procedure.'
  exit 1
fi
install -m 0755 "$binary" /usr/local/lib/chatbridge/frps
for file in frps.toml tls.crt tls.key; do install -o root -g chatbridge-relay -m 0640 "$credentials/$file" "/etc/chatbridge-relay/$file"; done
/usr/local/lib/chatbridge/frps verify -c /etc/chatbridge-relay/frps.toml
install -m 0644 "$(dirname "$0")/chatbridge-frps.service" /etc/systemd/system/chatbridge-frps.service
systemctl daemon-reload
systemctl enable --now chatbridge-frps
systemctl --no-pager --full status chatbridge-frps

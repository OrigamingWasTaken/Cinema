#!/bin/sh
# Runs with host networking: the first non-loopback IPv4 route is the NAS's LAN address.
NAME="${CINEMA_HOSTNAME:-cinema.local}"
IP="${NAS_IP:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')}"
if [ -z "$IP" ]; then
  echo "could not detect the NAS IP; set NAS_IP in .env" >&2
  exit 1
fi
echo "publishing $NAME -> $IP"
# -R: don't publish a reverse record (the NAS already has its own name).
exec avahi-publish -a -R "$NAME" "$IP"

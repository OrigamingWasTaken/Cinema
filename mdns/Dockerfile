# Publishes an mDNS name (default cinema.local) for the NAS through the host's
# avahi-daemon (DSM runs one), so phones can open http://cinema.local.
FROM alpine:3.20
RUN apk add --no-cache avahi-tools
COPY publish.sh /publish.sh
CMD ["sh", "/publish.sh"]

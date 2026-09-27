# Network Plan

Remote access uses Tailscale. Nothing is exposed to the internet and no
router ports are forwarded.

## Current state

- Tailscale account created. Phone, tablet, and Windows laptop are on the
  tailnet.
- The Tailscale login account must use two-factor authentication: whoever
  controls it can add devices to the tailnet.
- MagicDNS on, so devices are reachable by name.

## Laptop (interim host)

- Open WebUI is served to the tailnet with
  `tailscale serve --bg --http=80 3000`: plain HTTP inside the tailnet
  (the Tailscale tunnel encrypts it end to end), so no certificate is
  issued and no names are published. The setting persists across reboots.
  Undo: `tailscale serve --http=80 off`.
- Reach it from a phone or tablet with Tailscale connected at
  `http://<laptop name>/` (MagicDNS). Only 3000 is served; the chat API,
  Qdrant, and everything else stay on 127.0.0.1.
- HTTPS option: enable HTTPS Certificates in the Tailscale admin console,
  then `tailscale serve --bg --https=443 3000` (and turn the HTTP one off).
  Needed for installing it as a home-screen app and for microphone use.
  Trade-off: certificates are logged publicly (Certificate Transparency),
  which publishes the machine and tailnet names.

## Mac Studio setup day

1. Wired Ethernet, on the UPS.
2. Install Tailscale and sign in.
3. In the Tailscale admin console, **disable key expiry for the Mac**, so it
   doesn't drop off the tailnet after 180 days. Keep expiry on for other
   devices.
4. Enable macOS Remote Login (SSH); connect to it over Tailscale.
5. Services bind to `127.0.0.1` only (see docker-compose.yml). Expose the
   assistant's web page to the tailnet with `tailscale serve`, which adds
   HTTPS. Never use Tailscale Funnel (it publishes to the public internet).

## Later

- Access rules (ACLs): by default every tailnet device can reach every
  other. Restrict so only the user's own devices can reach the Mac, and the
  Mac can't initiate connections to them.

## Notifications and privacy

Push notifications pass through Apple and the notification provider. They
carry only content-free text ("Digest ready, 3 items need you") and a link.
The full digest opens from a page reachable only over Tailscale.
See docs/digest.md.

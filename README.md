<a href="https://lnbits.com" target="_blank" rel="noopener noreferrer">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://i.imgur.com/QE6SIrs.png">
    <img src="https://i.imgur.com/fyKPgVT.png" alt="LNbits" style="width:280px">
  </picture>
</a>

[![License: MIT](https://img.shields.io/badge/License-MIT-success?logo=open-source-initiative&logoColor=white)](./LICENSE)
[![Built for LNbits](https://img.shields.io/badge/Built%20for-LNbits-4D4DFF?logo=lightning&logoColor=white)](https://github.com/lnbits/lnbits)

# Tunnel Me Out

A minimal LNbits extension that requests a reverse proxy from a public URL you own so a local LNbits can be reached publicly.

- Enter days, click **Top up tunnel**.
- Pay the returned invoice; the extension verifies payment with the server and auto-activates.
- It saves the SSH key, runs the reverse tunnel, and shows the public URL.
- Reuse **Top up tunnel** to extend the same proxy; the private key and pending invoice survive page refreshes and restarts.

## Powered by LNbits

[LNbits](https://lnbits.com) is a free and open-source lightning accounts system.

[![Visit LNbits Shop](https://img.shields.io/badge/Visit-LNbits%20Shop-7C3AED?logo=shopping-cart&logoColor=white&labelColor=5B21B6)](https://shop.lnbits.com/)
[![Try myLNbits SaaS](https://img.shields.io/badge/Try-myLNbits%20SaaS-2563EB?logo=lightning&logoColor=white&labelColor=1E40AF)](https://my.lnbits.com/login)

## Server configuration and upgrades

Upgrade the Reverse Proxy extension on the public server **before** upgrading
this client. The client now requires its verified tunnel status endpoint. LNbits
applies migration `m004_tunnel_error` to store connection error details.

The default provider remains `https://lnbits.lnpro.xyz`. To use your own server,
set these environment variables before starting LNbits:

```sh
TUNNEL_ME_OUT_REMOTE_BASE=https://your-public-lnbits.example
TUNNEL_ME_OUT_PUBLIC_ID=the-public-id-from-reverse-proxy-settings
```

Use the same provider for an existing reservation. Changing the provider does not
move its keys or paid time. The local host/port default to the running LNbits
configuration; advanced overrides are optional.

The server owns the paid expiry. Payment confirmation, restart and reconnect copy
that expiry and never extend it locally. Payment checks run every five seconds,
so payment during a disconnect is recovered without a WebSocket notification.
An unpaid renewal leaves the existing connection running until its paid expiry.
Repeated clicks reuse an outstanding invoice until it expires.

The supervisor checks active tunnels every 30 seconds, keeps healthy SSH processes
running, and restarts failed processes. SSH uses only the reservation's identity,
disables interactive prompts and shared SSH connection configuration, and uses
keepalives to detect dead connections. Private key files are created atomically
with mode `0600` inside a `0700` directory. Shutdown cancels payment checks and
terminates/reaps managed SSH processes.

`active` means the server has provisioned paid time. Public reachability is checked
separately; a payment event does not imply that the URL is already reachable.
SSH errors appear on the tunnel card and in LNbits logs. Host keys use
`StrictHostKeyChecking=accept-new`: a changed server host key is refused. Verify a
legitimate host-key rotation with your server operator before updating the
corresponding managed `known_hosts` file.

Only run a copied connection script if the managed connection is stopped; two
clients cannot own the same remote listening port. Stop the whole LNbits service
process group on an unclean restart to avoid orphaned SSH sessions.

The paired Reverse Proxy README documents server SSH account permissions, Caddy
setup, recovery of older mismatched keys, and regression test commands.

## Testing both extensions locally

Keep this checkout next to `reverse_proxy` and run from their parent directory:

```sh
DEBUG=false LNBITS_DATA_FOLDER=/tmp/lnproxfix-tests uv run --project reverse_proxy --frozen --python 3.12 pytest --import-mode=importlib reverse_proxy/tests/test_paired_extensions.py -v
```

The paired test uses both real extension HTTP APIs, separate temporary databases,
and an isolated local SSH daemon. It covers purchase, refresh, missed payment
notification, HTTP forwarding, renewal, reconnect, restoration after shutdown,
and expiry. Install `ssh`, `ssh-keygen`, and `sshd`; the test skips if the server
binary is unavailable. Lightning invoices, superuser login and the Caddy reload
are simulated. No real payment, hosted tunnel provider, or production service is
used. See the paired test section of the Reverse Proxy README for details.

### Pydantic 2 compatibility

These checkouts target LNbits **1.7.0-rc1 or newer**, using Pydantic 2. Both
lockfiles use the same LNbits/Pydantic versions as the Tunnel Me Out migration.
Upgrade older LNbits installations before installing these extension versions.
The tunnel lifecycle changes remain in place; this compatibility update does not
require an additional database migration.

Model serialization and copies use `model_dump`, `model_dump_json`, and
`model_copy`. Optional fields explicitly default to `None`, following the
[Pydantic 2 migration guide](https://docs.pydantic.dev/latest/migration/).
Tests treat Pydantic deprecation warnings as errors, including the paired local
HTTP/SSH test, to prevent legacy model calls from being reintroduced.

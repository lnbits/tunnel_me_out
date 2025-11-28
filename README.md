# Tunnel Me Out

A minimal LNbits extension that requests a reverse proxy from satsy.co so a local LNbits can be reached publicly.

- Enter days, click **Top up tunnel**.
- Pay the returned invoice; the extension listens for payment and auto-activates.
- It saves the SSH key, runs the reverse tunnel, and shows the public URL.
- Reuse **Top up tunnel** to extend the same proxy; stale entries older than a week are pruned.

Configuration is hardcoded to the satsy service (`public_id=N5iicNjZz2fyMZtiD3zvxT`).

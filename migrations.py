
empty_dict: dict[str, str] = {}


async def m002_tunnels(db):
    """Single table to store the current tunnel details per user."""

    await db.execute(
        f"""
        CREATE TABLE tunnel_me_out.tunnels (
            id TEXT PRIMARY KEY,
            tunnel_id TEXT NOT NULL,
            subdomain TEXT NOT NULL,
            remote_port INT NOT NULL,
            ssh_user TEXT NOT NULL,
            ssh_host TEXT NOT NULL,
            ssh_private_key TEXT NOT NULL,
            ssh_command TEXT NOT NULL,
            public_url TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            payment_hash TEXT NOT NULL,
            payment_request TEXT NOT NULL,
            status TEXT NOT NULL,
            days INT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            updated_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
    """
    )

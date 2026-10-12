-- Demo tenants for local testing only. Keys are public on purpose; never reuse this file outside dev.
-- Fixed ids so README/EVIDENCE commands can reference them.
INSERT INTO tenants (id, name, api_key_hash) VALUES
    ('00000000-0000-7000-8000-00000000000a', 'Tenant A', encode(sha256('demo-key-tenant-a'), 'hex')),
    ('00000000-0000-7000-8000-00000000000b', 'Tenant B', encode(sha256('demo-key-tenant-b'), 'hex'));

# Security

Do not put pairing files, certificates with private keys, enrollment packages,
account identifiers, logs, dumps, database files or chat attachments in issues.
Use GitHub private vulnerability reporting when enabled, or contact the owner
through their public profile without attaching secrets.

Only PC B and the Windows VM hold endpoint/message keys. The relay holds its own
outer TLS key and FRP registration token; these cannot decrypt inner mTLS.
Metadata (connection times, duration and volume) remains visible. The fixed
client and Hook, the VM owner and an unlocked editor remain endpoint trust
boundaries. Local platform caches and explicitly retained diagnostic/content
logs can contain plaintext.

The public source archive is not a backup of personal identities or history.
Keep encrypted authority backups, VM/gateway backups and their passphrase
outside GitHub. A lost data key cannot be reconstructed from source code.

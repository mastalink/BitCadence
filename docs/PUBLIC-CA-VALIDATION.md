# Validate public CA material before persistence

The lab spoke previously wrote the entire tls-ca secret value to its trust-store
file after only checking for a certificate marker. Parse it as X.509 and serialize
only the public certificate before writing. Invalid material leaves the prior
file untouched, and appended private-key material is not persisted.

A focused regression generates a certificate/private key, verifies public-only
output, and checks private-key-only, malformed certificate and token inputs.
It passed locally after failing before the helper existed. AWS imports are
stubbed for this local unit test; it does not construct clients or claim a live
TLS/cloud acceptance result.

This isolated branch starts at e7e8ed9 on codex/bitcadence-completion. It addresses
the underlying input-to-disk boundary associated with CodeQL alert #26. Scanner
acceptance is still required; this change does not dismiss the alert, waive any
review gate or deploy the lab. Other active worker/MCP files are untouched.

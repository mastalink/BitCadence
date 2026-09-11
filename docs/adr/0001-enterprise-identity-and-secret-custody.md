---
status: accepted
---

# Federate human identity and keep provider secrets server-side

BitCadence will model human Users, Organization Memberships, Saved Instances, Sessions, and Device Credentials separately from agent bearer tokens. Human authentication is federated through OIDC first, then SAML or a trusted-header Adapter; LDAP directories normally connect through an enterprise identity broker, with direct LDAPS reserved for fully self-hosted deployments. External groups grant nothing until an Organization Role Mapping translates them to BitCadence roles and scopes.

Provider credentials never synchronize to browsers, phones, desktops, or apps. A Model Connection holds only a Secret Reference, and the gateway resolves it through a vault Seam. The local Adapter uses the encrypted SecretStore; the shared Adapter stores AES-256-GCM ciphertext in the shared database while its master key remains outside that database and can later be supplied by a cloud KMS or customer-managed vault.

## Consequences

- Browser Sessions use secure server-managed cookies; native clients use PKCE or approved device pairing.
- SCIM 2.0 will own durable user and group provisioning, including immediate deactivation.
- Database tenant enforcement and security audit events are required before calling the hosted posture enterprise-ready.
- Raw provider keys and long-lived organization-wide bearer tokens are not cross-device synchronization mechanisms.

# BitCadence Domain Language

BitCadence coordinates governed work across people and agents. This glossary fixes the language used for portable instances, enterprise identity, and secret custody.

## Language

**Organization**:
The tenant that owns memberships, saved instances, identity providers, model connections, and audit history.
_Avoid_: Tenant, account

**Saved Instance**:
A named BitCadence environment belonging to exactly one Organization and reachable by its members from multiple devices.
_Avoid_: Server profile, connection bookmark

**Membership**:
The relationship granting one User roles and scopes inside one Organization.
_Avoid_: Account role, org user

**Identity Provider**:
An Organization-configured source of human identity such as Okta, Entra ID, a SAML provider, or a trusted authentication proxy.
_Avoid_: Login backend, directory

**Role Mapping**:
An explicit Organization rule translating an Identity Provider group or entitlement into BitCadence roles and scopes.
_Avoid_: Automatic role claim

**Session**:
A short-lived, revocable authorization for one User on one browser or device.
_Avoid_: Login token, agent token

**Device Credential**:
A scoped, revocable credential issued to one approved desktop, CLI, MCP client, or worker installation.
_Avoid_: API key, master token

**Model Connection**:
Named provider metadata whose credential is held by a Secret Reference and used server-side.
_Avoid_: API key record, model account

**Secret Reference**:
A tenant-scoped pointer naming secret material without containing or exposing its value.
_Avoid_: Encrypted key, credential field

## Flagged ambiguities

- **Role** identifies a BitCadence authorization role. External IdP groups never become Roles until an explicit Role Mapping translates them.
- **API key** means a third-party provider credential. BitCadence Device Credentials and Sessions must not be called API keys.

## Example dialogue

> **Developer:** Joe signed in through the Acme Identity Provider. Why can he approve jobs?
>
> **Domain expert:** His Acme Membership received the Approver role through the `Change-Approvers` Role Mapping. His phone has a Session, but the Anthropic API key remains behind the Model Connection's Secret Reference in the Saved Instance.

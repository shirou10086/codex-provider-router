# Security

## Reporting

Please report vulnerabilities privately through GitHub Security Advisories:

https://github.com/shirou10086/codex-provider-router/security/advisories/new

Do not include credentials, request bodies, or private model catalog contents in a public issue.

## Credential Model

The router does not store upstream provider credentials. It invokes the authentication mechanism already configured in the user's Codex provider configuration.

The generated local client token is stored under `~/.codex/provider-router/client-token` with mode `0600`.

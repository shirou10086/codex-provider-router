# codex-provider-router

[![Latest release](https://img.shields.io/github/v/release/shirou10086/codex-provider-router)](https://github.com/shirou10086/codex-provider-router/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/shirou10086/codex-provider-router/ci.yml)](https://github.com/shirou10086/codex-provider-router/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

`codex-provider-router` is an unofficial local proxy that lets Codex switch between models from different Responses API providers without starting a new conversation.

It runs a small loopback-only HTTP server, rewrites a selected model alias to the correct `provider + upstream model`, and forwards the streaming Responses API request using the authentication already configured in `~/.codex/config.toml`.

This project is not affiliated with or endorsed by OpenAI.

## Why

Codex conversations are tied to the provider that created them. Changing a model chip alone can produce errors such as:

```text
model "foo" is not available for user ... in tenant ...
```

or:

```text
Invalid 'input[49].id': expected an ID that begins with 'fc'
```

The first error usually means the model was sent to the wrong provider. The second usually means tool-call identifiers from one API shape were passed into another. This router keeps the provider/model mapping in one place so switching models is deterministic.

## Features

- Route any model alias to any Responses API provider configured in Codex.
- Switch provider/model combinations from the normal Codex model picker.
- Preserve streaming Responses API events, including tool calls.
- Merge one or more model catalogs and expose stable public aliases.
- Reuse existing `auth.command`, `env_key`, or bearer-token provider settings.
- Never package or print upstream credentials.
- Bind only to `127.0.0.1` or `localhost`.
- Protect the local endpoint with a generated per-user bearer token.
- Retry once after `401` with refreshed credentials; do not retry `403`.
- Back up `~/.codex/config.toml` before every change.
- Restore the current provider/model with one `uninstall` command.
- Install a user LaunchAgent on macOS for automatic startup and restart.

## Requirements

- Python 3.11+
- Codex CLI installed and available as `codex`
- One or more custom providers in `~/.codex/config.toml`
- Every routed provider must use `wire_api = "responses"`

Example provider configuration:

```toml
[model_providers.provider_a]
base_url = "https://provider-a.example/v1"
wire_api = "responses"
env_key = "PROVIDER_A_API_KEY"

[model_providers.provider_b]
base_url = "https://provider-b.example/v1"
wire_api = "responses"

[model_providers.provider_b.auth]
command = "/usr/local/bin/provider-b-auth"
args = ["token"]
timeout_ms = 5000
refresh_interval_ms = 300000
```

## Install From GitHub

```bash
pipx install "git+https://github.com/shirou10086/codex-provider-router.git"
```

You can also download the wheel from the
[latest GitHub Release](https://github.com/shirou10086/codex-provider-router/releases/latest)
and install it directly:

```bash
pipx install codex_provider_router-0.1.2-py3-none-any.whl
```

If your current Codex config already points to a supported custom provider, generate a router config from it and install the service:

```bash
codex-provider-router install
```

To expose several providers in the model picker:

```bash
codex-provider-router install --reset-config \
  --route provider_a:model-a \
  --route provider_b:model-b
```

You can set a custom public alias with `ALIAS=PROVIDER:MODEL`:

```bash
codex-provider-router install --reset-config \
  --route fast=provider_a:small-model \
  --route deep=provider_b:large-model
```

Restart Codex Desktop after installation so it reloads the model catalog.

### Foreground mode

```bash
codex-provider-router install --no-service
codex-provider-router serve
```

## Commands

```bash
codex-provider-router status
codex-provider-router check
codex-provider-router routes
codex-provider-router catalog
codex-provider-router token
codex-provider-router uninstall
```

- `status` shows the current Codex provider, router health, and LaunchAgent state.
- `check` validates the catalog and upstream credentials without sending a model request.
- `routes` lists picker models and compatibility aliases.
- `catalog` prints the merged model catalog served to Codex.
- `uninstall` restores the current thread to the direct upstream provider and stops the service.

## Configuration

The installer writes:

```text
~/.codex/provider-router/config.json
~/.codex/provider-router/client-token
```

Example route:

```json
{
  "alias": "provider-b-model-b",
  "provider": "provider_b",
  "upstream_model": "model-b",
  "display_name": "Provider B - Model B",
  "public": true
}
```

Only routes with `"public": true` appear in the Codex model picker. Non-public aliases remain available for older threads.

Optional provider model catalogs can be added with `catalog_paths`:

```json
{
  "catalog_paths": [
    "/absolute/path/to/provider-model-catalog.json"
  ]
}
```

## How It Works

```text
Codex Desktop
    |
    | Responses API + local bearer token
    v
codex-provider-router (127.0.0.1)
    |
    | alias -> provider/model
    v
provider A / provider B / custom gateway
```

The router reads provider definitions from the existing Codex config and obtains upstream credentials only when forwarding a request. Request bodies, headers, and credentials are not logged.

## Security

- The service refuses non-loopback bind addresses.
- The client token is stored with `0600` permissions.
- Token comparisons use constant-time comparison.
- Provider credentials remain owned by the existing Codex configuration.
- `401` refreshes authentication; `403` is returned without retrying to avoid duplicate requests for permission errors.
- The macOS LaunchAgent runs as the current user and does not require `sudo`.

## Development

```bash
python3.13 -m pip install '.[dev]'
python3.13 -m unittest discover -s tests -v
python3.13 -m build
```

## License

MIT

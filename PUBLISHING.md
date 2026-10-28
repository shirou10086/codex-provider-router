# Publishing

## PyPI Trusted Publishing

The release workflow uses GitHub OIDC and does not store a PyPI API token.

Before the first release, configure a Trusted Publisher on PyPI:

- Owner: `shirou10086`
- Repository: `codex-provider-router`
- Workflow: `release.yml`
- Environment: `pypi`

If the PyPI project does not exist yet, create a pending publisher with the same values. Once the first `v*` tag is pushed, GitHub Actions builds and publishes the wheel and source distribution.

## Release

1. Update `version` in `pyproject.toml` and `__version__` in `src/codex_provider_router/__init__.py`.
2. Run the tests and build locally.
3. Commit and push `main`.
4. Push a matching tag:

```bash
git tag v0.1.0
git push origin main --tags
```

The workflow will:

- verify that the tag matches the package version;
- run the test suite;
- build the wheel and source distribution;
- create a GitHub Release with generated notes;
- upload both distributions to PyPI.

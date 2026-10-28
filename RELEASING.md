# Releasing

Releases are distributed through GitHub Releases. PyPI publication is intentionally not configured.

1. Update `version` in `pyproject.toml` and `__version__` in `src/codex_provider_router/__init__.py`.
2. Run the tests and build locally:

```bash
python3.13 -m pip install '.[dev]'
python3.13 -m unittest discover -s tests -v
python3.13 -m build
```

3. Commit and push `main`.
4. Push a matching tag:

```bash
git tag v0.1.1
git push origin main --tags
```

The GitHub Actions workflow verifies that the tag matches the package version, runs the tests, verifies installation from the built wheel, and creates a GitHub Release with the wheel and source distribution attached.

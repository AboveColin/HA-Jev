# The documentation site

Built with [mkdocs-material](https://squidfunk.github.io/mkdocs-material/) and
published to GitHub Pages by `.github/workflows/docs.yaml`.

```bash
pip install -r requirements-docs.txt
mkdocs serve      # http://127.0.0.1:8000, reloads as you type
mkdocs build --strict
```

`--strict` turns a dead internal link into a build failure. CI runs it on every pull
request, so a link that rots is caught before it ships rather than by a reader.

Every measurement quoted in these pages comes from `docs/measurements.md`, which is
the record of what was actually run. Do not write a number here that is not there.

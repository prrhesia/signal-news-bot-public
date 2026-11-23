# Contributing

Use [Development](docs/DEVELOPMENT.md) for setup and the code map. Keep
changes focused and add a regression that runs without live Signal,
production SQLite state, or hosted AI. Test with Python 3.11 or newer.

```sh
python -B -W error::RuntimeWarning -m unittest discover -s tests -q
```

- For media, feeds, or duplicate reports, create a minimal synthetic
  fixture. Never include private feed URLs, provider keys, Signal IDs,
  real group messages, or dashboard screenshots containing account data.
- Keep docs and the command guide in sync with user-facing changes.
- For updates, exercise the predecessor migration with synthetic data.
  Ask the maintainer before replacing any versioned release artifacts.
- Treat other contributors respectfully. Report security issues privately;
  see [SECURITY.md](SECURITY.md).

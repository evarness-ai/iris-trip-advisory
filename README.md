# Trip Advisory

An IRIS plugin, and the second half of a two-plugin collaboration: it consumes the
`weather.forecast` capability (provided by `iris-plugin-weather-now`) without importing it.
One read-only tool, `trip_advisory`: given a place and a date range it asks the forecast and
returns a trivial packing and outdoor-plans suggestion. Scaffolded with
`iris plugins new trip-advisory --kind tool`.

## How the two plugins connect

`manifest.yaml` declares `capabilities: uses: [weather.forecast]`; `setup(api)` calls
`api.capability("weather.forecast")`. IRIS mounts providers before consumers, governs and
audits every call, and returns `None` when nothing provides the capability.

- `uses` (what this plugin declares): with no provider the plugin still mounts and the tool
  says it could not check the weather and gives generic advice.
- `requires` (switch the manifest to it): with no provider the plugin does not mount; System
  Health shows `required capability not provided: weather.forecast`. Pinned by a test.

## Develop

```bash
pip install -e ".[test]"   # against an installed iris-harness wheel, never a source checkout
pytest                     # scripted model, network refused, stable-tier imports only
```

See `docs/GAPS.md` for the SDK shortcomings found while building this.

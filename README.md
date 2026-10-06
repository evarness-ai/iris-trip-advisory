# Trip Advisory

An [IRIS](https://github.com/evarness-ai/iris-harness) plugin, and the second half of a
two-plugin collaboration (the exercise of
[iris-harness#80](https://github.com/evarness-ai/iris-harness/issues/80)): it consumes the
`weather.forecast` capability (provided by
[`iris-plugin-weather-now`](https://github.com/evarness-ai/iris-weather-plugin)) without
importing it. It is built against iris_harness's stable tier only (`iris_harness.sdk`,
`iris_harness.testing`).
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

## Develop and test

iris-harness is not on a package index yet (iris-harness#108): build a wheel from a clone and
install that, never a source checkout.

```bash
pip install iris_harness-*.whl
pip install -e ../iris-weather-plugin   # optional: enables tests/graduation and the real-provider test
pip install -e ".[test]"
pytest                                  # scripted model, network refused, stable-tier imports only
```

`scripts/ci_local.sh` is the gate (fresh venv, ruff, black, `check_stable_imports`, pytest); it
installs a sibling `../iris-weather-plugin` checkout if present. `.github/workflows/ci.yml` is
manual-only until #108 is resolved.

## Graduation run

`tests/graduation/` and `docs/GRADUATION.md` hold the SDK graduation checklist run of
[iris-harness#81](https://github.com/evarness-ai/iris-harness/issues/81) against both plugins
(P0/P1 bullets and the G1-G10 verdicts). `docs/GAPS.md` lists the SDK shortcomings found while
building this plugin.

## License and contributing

Apache-2.0, the same license as iris-harness (see `LICENSE`). Contributions follow
[CONTRIBUTING.md](CONTRIBUTING.md).

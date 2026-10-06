# SDK gaps found while building this plugin

Each entry says what was tried, what happened, and the core change proposed. None is
worked around silently; where this repository carries a local bridge it is named. First written
against an earlier iris-harness build; re-checked on 2026-10-06 against the 0.1.0 wheel built
from `main` at `2166717`, in a fresh Python 3.12 venv, and each entry says what that changed
(tracking issues are in evarness-ai/iris-harness). Next to `iris-plugin-weather-now` next to `iris-plugin-weather-now` (see its own `docs/GAPS.md`; GAP-2 and
GAP-3 there, indexless install and scaffold ergonomics, apply here too and are not repeated).

## GAP-1 (important): a sync tool has no sanctioned way to call an async-only capability

Still open at `2166717` (iris-harness#107); the bridge's assumption (no running loop inside a
tool) was re-checked by `tests/graduation` and held.

- Tried: the scaffolded tool is `Callable[[dict], str]` (`PluginAPI.register_tool`), and
  `weather.forecast` is `async def forecast(...)` only (the Protocol), while the docs say a
  sync call inside a running loop "cannot be governed, so it is refused" and to "use the
  capability's async methods from async code".
- Result: nothing in the SDK says how a sync tool reaches an async capability. This plugin
  carries a bridge (`plugin._run_coroutine`: `asyncio.run`, or a one-thread executor when a
  loop is already running). It works because the loop runs sync tools on a worker thread
  today (observed: no running loop inside the tool), which is undocumented behaviour.
- Proposed core fix: let `register_tool` accept `async def` tools, or document and pin
  "tools run off the event loop" and give the SDK a `run_capability(coro)` helper, so every
  consumer does not reinvent the bridge.

## GAP-2: `weather.forecast` takes `days` from now, not a date range

Still open at `2166717` (iris-harness#107): `forecast(location, days=3)` is unchanged.

- Tried: the issue asks for "a location + date range". The Protocol is
  `forecast(location, days=3)`: N days ahead of today, no start offset.
- Result: the consumer converts `end - today` into `days`, caps it (14, a guess; the
  provider "may return fewer", the cap is not published), then filters the periods back down
  to the range. A trip starting in three weeks cannot be asked for directly, and a trip in
  the past cannot be distinguished from "outside the forecast horizon".
- Proposed core fix: `forecast(location, start=None, end=None, days=None)` or a published
  `max_days`/horizon on the spec, and a documented rule for ranges the source cannot cover.

## GAP-3: an undeclared capability use degrades the plugin with no reason

Corrected by `docs/GRADUATION.md` (GAP-5 there): System Health does give the reason; only
`Harness.plugins()` reports `("degraded", None)`, still true at `2166717`. The claim below that
the cause is only in the log was wrong.

- Tried: `api.capability("weather.forecast")` with `uses` removed from the manifest.
- Result: the call returns `None` and the plugin shows `('degraded', None)` in health: a
  status with no explanation (`h.plugins()[name]`), unlike `requires`, which says
  `required capability not provided: weather.forecast`. The cause is only in the log.
- Proposed core fix: put "capability weather.forecast not declared in manifest" in the
  health reason, and check statically at load that a `setup` using `api.capability` has the
  name in `uses`/`requires` where it can (or at least report it at first mount).

## GAP-4: no testing helper for the other half of a capability

Still open at `2166717` (iris-harness#107).

- Tried: test collaboration with both plugins mounted without importing the provider.
- Result: the consumer's test must hand-write a provider plugin (manifest mapping with
  `party`, `capabilities.provides`, a `setup` calling `api.provide`) to stand in for the
  real one. Mounting the real provider is possible only through its entry point, and then
  its HTTP cannot be injected (the weather plugin exposes `make_setup(transport)` only to
  code that imports it), so against the real provider the happy path is untestable offline;
  `tests/test_real_weather_provider.py` can only prove the degraded path under the refused
  network. It also loads the provider with `importlib` by entry-point name, which
  `check_stable_imports` (static) does not see: that is a test-only convenience, noted here.
- Proposed core fix: `iris_harness.testing.fake_provider("weather.forecast", impl)` returning
  a ready `InProcessPlugin` (manifest and `provide` included), and a documented way for a
  provider to expose its test seams (or a canned-response mode) to a consumer's harness.

## GAP-5: the scaffold has no consumer shape (extends weather GAP-3)

Still open at `2166717` (iris-harness#107); the scaffold's manifest now carries `party: untrusted`,
but it has no `capabilities:` block, and the generated README still says `iris plugin new`.

- `iris plugins new trip-advisory --kind tool` generates a word-counter tool, a manifest
  with no `party:`/`capabilities:` block, a model script and a test for the counter, and no
  `pytest-asyncio`. Everything consumer-specific (`capabilities.uses`, a degraded path,
  `content: external` for echoed third-party text, an in-test provider) was written by hand.
  The README it generates says `iris plugin new` (singular).
- Proposed: `--kind capability-consumer` (and `capability-provider`) producing a manifest with
  `uses`/`requires`, a `None` degraded branch, and a test with a stub provider.

## GAP-6 (noise, core wheel): harness startup logs a traceback in a core-only install

Mostly fixed on `main` (iris-harness#110 is still open). Re-checked at `2166717`: the
`googleapiclient` traceback and the `unknown handler` heartbeat warnings no longer appear, and
no `libc++abi` abort was seen and the gate's pytest step exited 0 (macOS). A new
warning appears on every chat turn instead: `intercept '<name>' is declared for plugin:<x> but
no plugin registered it; skipping`. The text below describes the earlier build.

- Every `harness(...)` start logs `skill discovery failed ... ModuleNotFoundError: No module
  named 'googleapiclient'` from the bundled `_data/config/skills/email/gmail-inbox/tools.py`,
  and a set of `heartbeat ... references unknown handler` warnings; the test process also
  exits with `libc++abi: terminating due to uncaught exception ... recursive_mutex lock
  failed` after a green run (macOS). Tests still pass; a third-party author reading the log
  cannot tell these are not their fault.
- Proposed: the wheel should not ship skills/heartbeats whose dependencies are optional, or
  discovery should skip them quietly when the extra is absent.

## Not checked

`iris plugins show trip-advisory` / a real profile: needs a running IRIS API. Mounting was
verified through the test harness with both plugins, in either list order.

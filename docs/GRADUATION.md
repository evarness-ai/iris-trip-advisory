# SDK graduation checklist: iris-weather-plugin + iris-trip-advisory

Run of issue evarness-ai/iris-harness#81 (P0 and P1), locally; no hosted CI ran. The suite was
first written against an earlier harness build and re-run on 2026-10-06 against a wheel built
from current iris-harness `main`; every verdict below is the result of that re-run.

* Harness: `iris-harness` 0.1.0 wheel built from `main` at `2166717` (includes the Governance
  Conformance Suite, #92), Python 3.12.13, macOS.
* Plugins: `iris-plugin-weather-now` (provider of `weather.forecast`, two read tools) and
  `iris-plugin-trip-advisory` (consumer, one read tool), both installed in ONE venv.
* Where: `tests/graduation/` in this repository (needs both plugins installed; without the weather
  plugin the directory is not collected and the report header says so). `scripts/ci_local.sh`
  installs the sibling weather checkout so the gate runs it.
* Result of the last full run: `scripts/ci_local.sh` (fresh
  venv, wheel built from `main` at `2166717`; ruff, black, `check_stable_imports`, pytest) passed
  with `102 passed, 3 xfailed` for the whole repository, with the weather plugin installed beside it.
  Each remaining `xfail` is `strict`: it states a behaviour the criteria need and the harness
  does not provide, and turns red the day it does. Against the earlier build the suite had
  eight xfails; five of them (three G10 audit-row tests, the Health optional-capability test
  and the approval-halt wording test) passed against current `main` as strict XPASS, and were
  converted into ordinary tests (the `test_gap_` prefix dropped). Three xfails remain: G3
  egress, external content scanned by default, and the plugin's own provider-failure
  handling.
* The Governance Conformance Suite (`assert_conformant` / `check_conformance`) is used for the
  audit, caller, approval and coverage checks instead of hand-rolled ones; everything beyond what
  it covers is written against the stable tier only (this suite is held to
  `check_stable_imports` itself; the only non-stable reads are of harness internals through
  `Harness._runtime`, as `iris_harness.testing.conformance` does itself, see GAP-5).

Reproduce: `pip install -e ../iris-weather-plugin -e ".[test]"` into a venv that has the harness
wheel, then `python -m pytest tests/graduation`. `test_p0_closure.py` creates a second venv and
needs the harness wheel (`IRIS_WHEEL_DIR`, default `../wheelhouse`; a missing wheel fails, it does
not skip) and a package index or pip cache for the harness's dependencies.

Test-only plugins live in `tests/graduation/` (the `ledger` write tool, raising plugins, an
identity source, stand-in providers, probes) and ship in neither package.

## Per-bullet results

PASS means the bullet was exercised and held. "GAP-n" points at the findings below.

| # | Bullet | Result | Evidence (test module `tests/graduation/...`) |
|---|---|---|---|
| P0-1 | CI runs `check_stable_imports()` | PASS | `test_p0_tools_and_audit.py`: both repos' `src` + `tests` have zero violations; negative control (a file importing `iris_harness.kernel...` is reported); the call is present in both `ci.yml` and `ci_local.sh` of both repos. `ci_local.sh` was executed end to end (step "only the stable tier is imported" passed). |
| P0-2 | One deterministic read tool | PASS | `weather_code_meaning`: same observation on two separate harnesses; PRE+POST rows, caller `model:system`, `audit_gaps() == []`; on `chat` and `chat_stream`; `assert_conformant` clean. |
| P0-3 | One network-backed tool | PASS (egress itself: GAP-1) | `weather_forecast` over `httpx.MockTransport`: only `geocoding-api.open-meteo.com` and `api.open-meteo.com`, https only; against the real transport the socket is refused by `no_network()` and the tool answers with an error observation (no crash), attempts limited to those two hosts. `chat` and `chat_stream`. |
| P0-4 | PRE_TOOL_USE and POST_TOOL_USE rows exist | PASS | Read from the ledger for the model's call (caller `model:system`) and for the capability call (`capability:weather.forecast.forecast`, caller `plugin:trip-advisory`, provider `weather-now`); `assert_conformant` on both plugins (tools + capability method) clean; negative control: an unexercised tool is reported as a `coverage` violation. |
| P0-5 | Undeclared tool refused / degrades correctly | PASS | Registering a tool not under `tools:` is refused (`ValueError ... not declared under 'tools:' (ADR-0110)`), the plugin is `degraded` (declared tool still served), the ghost tool is `unknown tool` to code callers and to the model, its function never runs (side-effect list stays empty), Health shows `[yellow] ... not declared`. |
| P0-6 | Throw inside the plugin: fault boundary contains it | PASS (one plugin finding: GAP-6) | Raising tool: turn still answered, plugin `degraded` with `failure_count` and `tool:boom: RuntimeError: kaboom`, PRE and POST rows still written, sibling plugin untouched and answers the next turn, Health yellow with the reason. Raising `setup`: `failed`, others mount. Raising provider: charged to the provider, consumer turn survives. |
| P0-7 | Uninstall + reboot IRIS | PASS | `test_p0_closure.py`, a real second venv: A both installed; B `pip uninstall` weather-now; C uninstall trip-advisory; D reinstall both. Site-packages diff after uninstall is exactly the plugin's files (nothing else removed or added); entry point and import gone; reboot with a stale profile names the missing plugin (`failed`, `not found as builtin, entry point, or ...`, Health red) and drops its tools and capability provider; reboot with a clean profile has no trace of it (registry, Health, tool pool, ledger, files in the home); consumer keeps working degraded; with both removed the core boots (`system` loaded, only `system_health`); reinstall restores the baseline exactly. |
| P0-8 | Full plugin suite under `no_network()` + fake model | PASS | `test_p0_offline.py` runs each repo's own suite in a subprocess with the `offline_guard` pytest plugin (every test inside `no_network()` + `use_fake_model(Script(()))`): weather 23 passed, trip 20 passed, 0 skipped/failed, guard wrapped every test, 0 stray socket attempts; negative control: a test that opens a socket fails under the guard. |
| P1-1 | Missing optional dependency (degraded) | PASS (GAP-7) | trip-advisory with no provider: mounted `loaded`, answers "Could not check the weather: no weather forecast service is installed ... pack light layers and a rain jacket"; still passes the conformance suite; a provider appearing ends the degraded mode with the same script. |
| P1-2 | Missing required dependency (mount refusal + health reason) | PASS | Missing required capability, package and env var each give `failed` with an exact reason (`required capability not provided: weather.forecast (no mounted plugin provides it)`, `required package not installed: <pkg>`, `required environment variable unset: <VAR>`), `setup` never ran, tool absent from the pool, `system_health` shows `[red]` with the reason and the next step (`iris plugins show <name>`); supplying the dependency mounts the same plugin. |
| P1-3 | Unauthorized tool denied by caller policy | PASS | Probe plugin without `uses: tools` calling `weather_forecast`: `held`, `caller_policy` deny row naming `plugin:<probe>`, no HTTP left the weather plugin, no POST row; with the grant it runs (and only that tool); an unmounted caller is refused; operator `tool-access.yaml` denial overrides a plugin's own grant, can take a whole capability or one method away (the real consumer degrades), and an unreadable file fails closed. |
| P1-4 | Write needing approval: reject = zero side effects; approve = exact pinned args, no post-approval mutation | PASS | Test-only `ledger_append` (`effect: write`, `approval: pinned`). From code and from the model (`chat`, `chat_stream`): held with the exact call on the approval row, nothing written; reject: tool function never ran, no POST row, caller told; approve: runs once with the pinned arguments even though the caller mutated its dict after queueing, the args digest of the queued row equals the digest of the run row, second answer refused, outcome delivered to `on_approved_call`; two queued calls are independent; the conformance suite agrees. |
| P1-5 | Capability provider removed: consumer degrades per uses/requires | PASS (no public runtime unmount: GAP-10) | `uses`: provider absent, provider whose `setup` fails after `provide`, provider removed while the consumer runs (`CapabilityUnavailable ... no longer mounted`, consumer degrades, provider not called again, consumer not blamed), real provider taken out; `requires`: consumer refused with the reason. The real uninstall is P0-7. |
| P1-6 | Capability result masking / external-content treatment across the boundary | masking PASS; external content NOT-EVIDENCED, default FAIL (GAP-2) | Masking: the owner's email in the provider's result reaches the consumer and the model as `[owner:email#1]`, provider's object untouched (copy), the literal never appears in the ledger, only a manifest `unmask` grant restores it and then the run is marked `personal`; a plain tool result is not masked (documented asymmetry, pinned). External: with `IRIS_GOVERNANCE_PROMPT_GUARD=1` the guard runs on the weather tool, on the capability result and on trip-advisory's result, none on internal tools, but with no classifier weights it reports `guard unavailable` and passes the text through verbatim; with the default configuration the guard does not run at all. Detection was not observed. |

## Gaps found (mechanism, evidence, proposed change)

None is worked around. `GAP` entries that are `xfail(strict)` in the suite are marked (xfail).

**GAP-1 (important, core): a plugin's own outbound HTTP is neither governed nor recorded.**
Mechanism: the only governance on a tool's network use is the `network_egress` PRE_TOOL_USE hook,
which inspects only tool names in its built-in list and a coding-agent persona's domain allowlist;
for `weather_forecast` its row is `network_egress: not a network tool`. The manifest has no
egress field (weather GAP-1, re-verified here), so nothing declares or checks the hosts. Evidence:
the weather tool contacted two hosts and the ledger never names either (`test_g3_*`, and the
(xfail) `test_gap_g3_*` wanting a row with the destination). Still open at `2166717`
(iris-harness#103). Proposed: manifest egress hosts compiled into the kernel policy, a governed
HTTP client for plugins, a drift check, a generic `sends_to`.

**GAP-2 (important, core; iris-harness#104, open): `content: external` is only scanned when an opt-in guard is on, and the
guard fails open.**
Mechanism: `_prompt_guards_from_env()` returns no retrieved-content hook unless
`IRIS_GOVERNANCE_PROMPT_GUARD` is truthy; when it is on and the classifier model is absent the
hook allows with a `warn` row `guard unavailable`. The weather manifest comment as first
written ("scanned for injected instructions before the model reads it") was therefore not true
by default (xfail `test_gap_external_content_is_scanned_by_default`) and has been corrected to
say the scan is opt-in; an injected instruction in a provider's
result reached the model verbatim in both configurations (`test_the_injected_text...`). The
declaration does reach the guard for the tool result and the capability result (verified).
Proposed: say in the plugin docs that scanning is opt-in; make "guard unavailable" visible in
Health; ship a no-weights heuristic floor so detection is testable offline.

**GAP-3 (core, health): FIXED on `main` (iris-harness#106, PR #120).** A consumer degraded by an
absent optional capability was reported `[green]` against the earlier build. Against `2166717`
the Health line names `weather.forecast` and is not green
(`test_health_reports_a_missing_optional_capability`, no longer an xfail).

**GAP-4 (core, audit; G10): FIXED on `main` (iris-harness#105, PRs #124 and #131).** Against the
earlier build a tool row did not name the plugin that owns the tool, the ReAct step's model call
did not name the model, and the stable `TurnAuditRow` carried no caller. Against `2166717` all
three hold (`test_g10_a_tool_row_names_the_plugin_that_owns_the_tool`,
`test_g10_every_model_call_row_names_the_model`,
`test_g10_the_stable_audit_row_type_carries_the_caller`, no longer xfails).

**GAP-5 (testing helper): `Harness.plugins()` returns `(status, load_error)` only.** A degraded
plugin shows `("degraded", None)`. The reason exists on the registry record and in Health. This
supersedes trip-advisory GAP-3, whose claim that "the cause is only in the log" is wrong (still
`("degraded", None)` at `2166717`, checked by `test_p1_dependencies.py`): Health
says `1 failure(s); last: capability:weather.forecast: ValueError: plugin 'trip-advisory' asked
for capability ... but its manifest does not declare it ...`. (`test_p1_dependencies.py`.)
This suite reads harness internals through `Harness._runtime` (the plugin registry for
`failure_count` / `last_error` / provider lists, the tool service to call as a stamped caller, and
the provider-status flip in GAP-10), the same way `iris_harness.testing.conformance` does,
because no stable accessor exists; `Harness._started` and `audit_db` are used to read raw ledger
payloads.

**GAP-6 (plugin, trip-advisory): only `CapabilityUnavailable` is caught from the provider.** A
provider raising anything else is charged to the provider (correct) and propagates through
`trip_advisory`, so the consumer is also charged and the model gets an error instead of the
degraded advice (xfail `test_gap_a_provider_failure_is_not_charged_to_the_consumer_too`). Fix in
the plugin: take the degraded branch for any provider exception, per the contract's consumer
section.

**GAP-7 (contract): there is no optional PACKAGE.** `requires.packages` is all-or-nothing;
only a capability (`uses`) can be optional (`test_the_manifest_has_no_way_to_call_a_package_optional`).

**GAP-8 (observations, not scored as defects).**
(a) An exception's text goes into the model's observation (`boom is unavailable (plugin 'boomer'
raised RuntimeError: kaboom). Answer without it.`); the plugin author's message reaches the
model. (b) A deliberate `CapabilityUnavailable("service unreachable")` from the real provider
still counts as a failure on the provider: after one refused-network call `weather-now` was
`degraded` with a failure count (`test_an_unreachable_service_reported_by_the_real_provider...`),
so a transient upstream outage turns the provider yellow. (c) With the router model unscripted the harness falls back to keywords, and "What does
weather code 61 mean" is routed to the coding agent because of the word "code"; graduation tests
use phrasing that reaches the loop. Whether the live router does the same was not run. (d) Against the
earlier build every harness start logged a `googleapiclient` traceback and `unknown handler`
heartbeats (trip GAP-6). Against `2166717` neither appears (checked: a bare `harness()` start
logs only a default-channel warning); but every chat turn in a core-only install now logs about
ten `intercept '<name>' is declared for plugin:<x> but no plugin registered it; skipping`
warnings (iris-harness#110, still open).

**GAP-9 (core wording): FIXED on `main` (iris-harness#109, PR #117).** A pinned write halted the
chat with "That would delete or overwrite your data", wrong for an append. Against `2166717` the
halt message no longer says delete
(`test_the_chat_halt_message_does_not_claim_a_write_deletes_data`, no longer an xfail).

**GAP-10 (core): there is no public way to unmount a plugin in a running process.** Removal is
a restart. The "provider removed while the consumer runs" test therefore flips the registry
record to `failed`, the state the registry itself uses, and the real removal is P0-7.

**Plugin sync-to-async bridge (trip GAP-1) re-verified, still open:** tools run with no running
event loop on both `chat` and `chat_stream` (`test_tools_run_with_no_running_event_loop...`), which
the plugin's bridge relies on and nothing documents.

## Milestone exit criteria G1-G10

The criteria were scored as stated in the task; where the wording could mean two things the
reading used is given. PASS = shown by a run in this suite; FAIL = a run showed it does not
hold; NOT-EVIDENCED = this run cannot show it.

| G | Criterion | Verdict | Evidence / why |
|---|---|---|---|
| G1 | Core purity | PASS | The installed harness has no reference to either plugin (`test_g1_*`: scan of every text file for plugin names/modules; only help-text examples of the string `weather-now` in the scaffold CLI, allow-listed by name); both plugins import only the stable tier (P0-1); the core boots with both removed (P0-7 C). Bounded by: the core publishes the `weather.forecast` capability and its `Forecast` type, so a new cross-plugin pair needs an SDK release (closed catalogue by design). |
| G2 | SDK sufficiency | PASS, with caveats | Both plugins were built, mounted, governed and removed with the stable tier only, no core edit, no private import. Caveats that needed local work-arounds or are missing: no governed egress (GAP-1), sync tool to async capability bridge (trip GAP-1, relies on undocumented threading), `weather.forecast` takes `days` not a date range and has one `temperature_c` (trip GAP-2, weather observation), scaffold has no provider/consumer shape, no installable index (weather GAP-2/3, trip GAP-5; not re-run here). |
| G3 | Governance passage | FAIL | Every tool call, capability call and held write passed PRE/POST_TOOL_USE with a stamped caller, and approvals pinned exact arguments (PASS on those). But a plugin's outbound HTTP passes through no governance and leaves no record (GAP-1), and the external-content scan is off by default and fails open (GAP-2). |
| G4 | Plugin isolation | PASS, scoped to faults and attribution | Raising tool / setup / provider are contained and charged to the right plugin; caller identity is the harness's stamp; capability results are masked copies; an undeclared tool or capability is refused. NOT claimed: adversarial isolation. `trust: in-process` is a contract, and `test_g4_*` shows a plugin can call another plugin's function directly with no row at all. |
| G5 | Tier clarity (read as: which model tier each step uses and what data class gates it) | PASS for what ran; NOT-EVIDENCED for plugin-initiated model calls | Every model call has an `egress_gate` row with `target_tier` (`tier_1` routing, `tier_2` ReAct, never `tier_3`) and classification; a capability result carrying the owner's (masked) email made the next model call `personal`, still on a local tier. Neither plugin makes a model call, and no cloud tier exists in the harness, so a `personal` request to a cloud tier being refused was not exercised. |
| G6 | Cross-plugin contract | PASS | Typed `weather.forecast` between two real packages that import each other nowhere: governed per call, caller stamped, provider named, owner identity masked in a copy, `uses` degrades, `requires` refuses, removal handled, mount order irrelevant (trip's own tests plus P1-5). The schema limits are GAP in the capability, not in the contract. |
| G7 | Upgrade stability (works on two consecutive versions) | NOT-EVIDENCED | Only one harness version exists (0.1.0, no tags, no published wheel; iris-harness#108); both plugins pin `>=0.1,<0.2`. A second release to run against does not exist. |
| G8 | Removal closure | PASS | P0-7 in a real second venv (files, interpreter, entry points, registry, tool pool, capability providers, ledger, home files; consumer degrades; core boots with both gone; reinstall is the exact inverse). |
| G9 | Offline verification | PASS, with a caveat | Both full suites pass under `no_network()` + empty-script fake model with nothing skipped, and every governance check above ran on the scripted model. Caveat: the closure test creates a venv and installs the harness's dependencies, so it needs an index or pip cache; and injection DETECTION cannot be shown offline (GAP-2). |
| G10 | Explainability: model / tier / caller / governance decision / plugin identity end to end | PASS (was FAIL against the earlier build; GAP-4) | Visible in the ledger, each asserted by a test: tier and classification per model call, the model on every ReAct-step model call, caller (`model:<agent>` or `plugin:<name>`), each hook's decision and reason, the owning plugin on a plain tool's rows, provider on capability rows, and a caller field on the stable audit row type (`test_g10_*`). Not run: the model and plugin identity of calls made by a plugin that itself calls a model (neither plugin does). |

## Re-run 2026-10-09 (after the egress work)

Run locally; no hosted CI. The 2026-10-06 results above are kept as written; this section
supersedes the G3 verdict and the two affected gaps.

* Harness: `iris-harness` 0.1.0 wheel built from `main` at `cbbfaec` (after #170 manifest
  `egress`, #171 governed client `api.http`, #172-#175 first-party plugins on it, #137 default-on
  external-content floor), Python 3.12, macOS.
* Plugins: `iris-weather-plugin` at `0b96d14` (PR evarness-ai/iris-weather-plugin#1: manifest
  `egress:` hosts, forecast tool and capability on `api.http`; not yet merged when this ran),
  `iris-plugin-trip-advisory` from this branch.
* Result: `scripts/ci_local.sh` (ruff, black, `check_stable_imports`, pytest) passed with
  `103 passed, 2 xfailed` for the whole repository (`tests/graduation`: `83 passed, 2 xfailed`
  on the same code). Against unmodified `main` plugins and the old tests the graduation suite
  ran `5 failed, 77 passed, 3 xfailed`: no strict xfail flipped until the weather plugin
  declared its egress.

What changed, per test:

| Test | Was | Now | Why (core change) |
|---|---|---|---|
| `test_g3_a_plugin_network_call_leaves_a_governed_row_naming_the_destination` | strict xfail (GAP-1, #103) | ordinary test | `pre_egress`/`post_egress` rows name both hosts, `https`, plugin `weather-now`, tool `weather_forecast`, decision `allow`; no row carries the place name (#170, #171; weather PR#1) |
| `test_g3_..._never_names_the_host` | asserted the ledger did not name the hosts | replaced by `test_g3_the_weather_tool_reaches_only_its_declared_hosts` | the old assertion was the gap itself |
| `test_both_rows_name_the_tool_and_a_stamped_caller` | every row `allow` | `allow`, or `transform` from `external_content_floor`; the caller is still asserted on each row | the floor adds a "marked untrusted" row to `content: external` results (#137) |
| `test_the_injected_text_..._is_redacted_by_the_floor_when_the_guard_is_unavailable` | injected text reached the model verbatim | the text is absent, the redaction marker and a floor row are present; the guard's `guard unavailable` warn row is still asserted | default-on floor (#137, #150, #159) |
| `test_gap_external_content_is_scanned_by_default` (#104) | strict xfail, whole claim | strict xfail, narrowed | still true: the classifier-backed `prompt_guard_retrieved` guard is opt-in (`IRIS_GOVERNANCE_PROMPT_GUARD`) and fails open when its model is absent. Closed by the floor: the injected text no longer reaches the model unredacted by default |
| `test_network_tool_against_the_real_transport_is_refused_not_crashed` | host names read from `no_network()` attempts | hosts read from `pre_egress` rows; attempts must be non-empty | the governed client resolves the name and connects to the checked address, so `no_network` records IPs (#171) |
| `test_c_with_both_removed_the_core_still_boots` | tools `["system_health"]` | `["search_docs", "system_health"]` | core registers `search_docs` (#143) |
| `test_b_reboot_with_a_clean_profile_has_no_trace_of_the_provider` | no health row names "weather" | the only such row is the consumer's own optional-capability degraded row | a plugin missing an optional capability is reported degraded (#120) |
| `test_a_both_plugins_are_found_only_through_their_entry_points` | failed: no `capability:` row | passes | not a harness change. The closure probe used fixed trip dates (2026-10-06/07); once past, the consumer returns `error: the trip is in the past` before it calls the capability. Dates are now relative to today |

Unchanged: the provider-failure xfail (`test_gap_a_provider_failure_is_not_charged_to_the_consumer_too`,
the trip-advisory tool catches only `CapabilityUnavailable`) still xfails.

Verdicts after the re-run (only G3 changes; the rest were re-run unchanged and hold):

| G | Verdict | Note |
|---|---|---|
| G1 | PASS | unchanged |
| G2 | PASS | the "no governed egress" caveat is closed |
| G3 | PASS for egress; PASS for the default scan with one open half | egress is declared, allowed and recorded. The injected-text half: the floor redacts by default (#137), the classifier guard stays opt-in and fail-open (#104, narrowed). This split is a reading; the owner decides whether the open half keeps G3 at FAIL |
| G4-G6, G8-G10 | PASS | unchanged (G8 now needs the relative-dates fix above) |
| G7 | NOT-EVIDENCED | untouched; tracked in iris-harness#108 |

Not run here: GitHub CI, a live model, a real Open-Meteo call (note: the real-transport test
resolves DNS for the two hosts before the socket is refused, so it is not strictly offline at
the name-resolution step), the classifier weights.

## What was not run

GitHub CI, a live model, a real Open-Meteo call, the live router,
`iris plugins show` against a running API (both plugins' own GAPS files say the same), a second
harness version, a cloud tier, and the classifier weights for the prompt guard.

# Contributing

This repository follows the contribution policy of
[iris-harness](https://github.com/evarness-ai/iris-harness/blob/main/CONTRIBUTING.md).
The parts that apply here:

- **DCO, no CLA.** Every commit carries a `Signed-off-by:` line certifying the
  [Developer Certificate of Origin](https://developercert.org/): commit with `git commit -s`.
  A GitHub no-reply address is fine.
- **Titles:** `<type>(<scope>): <summary>`, type one of `feat`, `fix`, `docs`, `style`,
  `refactor`, `test`, `chore`, `ci`, `perf`, `build`; scope optional.
- **No AI attribution** in commits, pull requests or credits: no `Co-Authored-By` trailers for
  tools, no "generated with" footers. You are the author of what you submit.
- **Stable tier only.** Import `iris_harness.sdk` and `iris_harness.testing`, nothing else from
  iris_harness; `scripts/ci_local.sh` checks it with `check_stable_imports`.
- **Style:** Black (line length 100) and Ruff; no emojis in code, docs or commits.
- **Tests never reach the network or a real model.** Run `scripts/ci_local.sh` before opening
  a pull request.
- Security issues are not reported in public; follow the iris-harness `SECURITY.md`.

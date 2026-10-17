bump: patch
**The old `zeo_core.integrations.github.app_tokens` path forwards every name, and the SOW-36 fence is harder to get around.** `Access` and private helpers still resolve through the old path; the string-import ban fails if grep cannot run, is tested against a planted string, and its limits are written down; the package-root contract also forbids submodules of a moving integration.

bump: patch
Release pipeline only; nothing under `src/` changes. The smoke test now retries its install from PyPI while the index catches up (the 0.55.3 and 0.55.4 runs passed the wait probe and then failed the install), and the documentation workflow requests an errored GitHub Pages build again, up to three times, before it fails.

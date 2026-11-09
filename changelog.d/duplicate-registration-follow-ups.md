bump: patch
**Docs and tests for the duplicate-registration guard.** A docs section for the duplicate error, the two row fields and `id_mismatches`; a test that pins the sorted order of the distributions in the message; a distribution with no name or version is no longer shown as `None None`; the test sites are removed by monkeypatch.

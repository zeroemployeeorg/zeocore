bump: patch
Test only: the release digest step must fail on a partial build (a wheel with no sdist), which pins `set -euo pipefail` in `publish.yml`. No workflow, runtime or dependency change.

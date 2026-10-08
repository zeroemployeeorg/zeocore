# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.5.x | Yes |
| < 0.5 | No |

ZeoCore is beta. Security fixes land on the current minor line.

## Reporting a vulnerability

Do **not** open a public GitHub issue for security problems.

1. Use [GitHub Security Advisories](https://github.com/zeroemployeeorg/zeocore/security/advisories/new)
   on this repository, or
2. Email the maintainer listed in `pyproject.toml` (`rod@aiproduct.engineer`)
   with a description, impact, and steps to reproduce.

Please include the zeocore version (`import zeo_core; print(zeo_core.__version__)`),
Python version, and whether extras (`http`, `mcp`, …) are involved.

You should receive an acknowledgement within a few days. We will discuss a
fix and a disclosure timeline before any public advisory.

## Scope

In scope: vulnerabilities in `zeo_core` as published on PyPI (including
optional adapters). Out of scope: issues in third-party extras' upstream
packages, or misconfiguration of secrets in your own `.env` / YAML.

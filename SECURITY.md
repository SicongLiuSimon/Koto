# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | :white_check_mark: |
| < 1.1   | :x:                |

Only the latest release line receives security updates. Users on older versions should upgrade.

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Instead, please use one of the following methods:

1. **GitHub Private Security Advisory** (preferred): Navigate to the [Security Advisories](../../security/advisories) tab of this repository and click **"Report a vulnerability"**.
2. **Email**: Send a detailed report to `security@example.com`.

> **Note:** The email address above is a placeholder. Repository maintainers should replace it with an actual monitored security contact.

## What to Include

To help us triage and resolve the issue quickly, please provide:

- **Description** — A clear summary of the vulnerability.
- **Reproduction Steps** — Step-by-step instructions to reproduce the issue, including any relevant configuration, environment details, or proof-of-concept code.
- **Impact Assessment** — Your understanding of the severity and potential impact (e.g., data exposure, remote code execution, denial of service).
- **Affected Versions** — Which version(s) you observed the issue on.

## Response Timeline

| Milestone                | Target        |
| ------------------------ | ------------- |
| Initial acknowledgement  | Within 48 hours |
| Fix timeline provided    | Within 7 days   |
| Patch release (critical) | As soon as possible |

We will keep you informed of our progress throughout the remediation process.

## Scope

### In Scope

- Koto application (desktop and packaged builds)
- Web interface
- API endpoints
- Configuration and authentication mechanisms

### Out of Scope

- **Third-party dependencies** — If the vulnerability originates in an upstream library, please report it directly to that project's maintainers and let us know so we can track it.
- Vulnerabilities requiring physical access to the host machine.
- Social engineering attacks.

## Recognition

We appreciate the efforts of security researchers who help keep Koto safe. Contributors who report valid vulnerabilities will be credited in the [CHANGELOG](CHANGELOG.md) upon release of the fix, unless they prefer to remain anonymous.

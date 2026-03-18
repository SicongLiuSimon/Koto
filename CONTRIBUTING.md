# Contributing to Koto

Thank you for your interest in contributing to Koto! This guide will help you
get started.

## Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/your-org/koto.git
cd koto

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# 3. Install dependencies
pip install -r config/requirements.txt

# 4. Run the application
python Koto_Start.py
```

## Project Structure

```
Koto/
├── app/            # Core application logic
│   ├── core/       # Agent framework, plugins, services
│   └── api/        # API route blueprints
├── web/            # Flask web layer (app.py, auth.py)
├── config/         # Configuration and requirements
├── tests/          # Test suite
│   └── unit/       # Unit tests
├── docs/           # Documentation
└── scripts/        # Build and utility scripts
```

## Code Style

- **Formatter**: [Black](https://black.readthedocs.io/) (line length 88)
- **Import sorting**: [isort](https://pycqa.github.io/isort/) (Black-compatible profile)
- **Linter**: flake8 or ruff (optional)
- **Type hints**: Encouraged for public APIs

Run before committing:

```bash
python -m black app/ web/ tests/
python -m isort app/ web/ tests/
```

## Testing

We use **pytest** for all tests:

```bash
# Run all unit tests
python -m pytest tests/unit/ -q

# Run with coverage
python -m pytest tests/unit/ --cov=app --cov=web --cov-report=term-missing

# Run a specific test file
python -m pytest tests/unit/test_auth_coverage.py -v
```

### Writing Tests

- Place unit tests in `tests/unit/`
- Name test files `test_<module>.py`
- Use `@pytest.mark.unit` for unit tests
- Use `monkeypatch` or `unittest.mock.patch` for isolation

## Pull Request Process

1. **Fork** the repository and create a feature branch from `main`
2. **Write tests** for any new functionality
3. **Run the test suite** and ensure all tests pass
4. **Format your code** with Black and isort
5. **Write a clear PR description** explaining what changed and why
6. **Keep PRs focused** — one feature or fix per PR

## Security

- Never commit secrets, API keys, or credentials
- Use environment variables for sensitive configuration
- Follow the sandbox patterns in `app/core/agent/plugins/` when adding
  new code execution features
- Report security vulnerabilities privately (see [SECURITY.md](SECURITY.md))

## Commit Messages

Use clear, descriptive commit messages:

```
feat: add AST validation to python_exec sandbox
fix: correct rate limit window calculation
docs: update architecture diagram
test: add coverage for JWT token expiry
```

## Questions?

Open a [Discussion](https://github.com/your-org/koto/discussions) or file an
issue if you need help getting started.

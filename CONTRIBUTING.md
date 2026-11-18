# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation.
3. Run the Ruff checks and tests described in [Local development](docs/DEVELOPMENT.md).
4. Test you contribution.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People *love* thorough bug reports. I'm not even kidding.

## Use Conventional Commits

This project uses [Conventional Commits](https://www.conventionalcommits.org/) and
[release-please](https://github.com/googleapis/release-please) for automated releases.

Format: `<type>: <description>`

| Type | Version Bump | Example |
|------|-------------|---------|
| `fix:` | Patch (1.0.x) | `fix: resolve re-auth loop` |
| `feat:` | Minor (1.x.0) | `feat: add switch platform support` |
| `feat!:` | Major (x.0.0) | `feat!: redesign configuration flow` |
| `chore:` | None | `chore: update dependencies` |
| `docs:` | None | `docs: update README` |

## Use a Consistent Coding Style

Use the pinned [Ruff](https://docs.astral.sh/ruff/) version in `requirements.txt`
for linting and formatting.

## Test your code modification

See [Local development](docs/DEVELOPMENT.md) for the pinned Home Assistant
container, HTTPS Gira simulator, regression tests and complete HA smoke test.
The demo is disposable and separate from the real installation. Hardware checks
are still required before a release.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.

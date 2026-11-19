# Contributing

Thanks for looking at snapgrid. Issues and pull requests are welcome.

## Before you start

- For a starter issue, claim it with a comment first, so two people do not build the same thing.
- Keep each change small and focused on one issue.

## Running the tests

```console
python3 run_tests.py
```

The full suite (30 tests) must pass before a pull request is reviewed. The measured recovery numbers in the README (gentle, harsh and held-out suites) must not go backwards: a change that recovers more sprites by also producing more wrong grids is a regression, not an improvement — abstaining is the designed behaviour when no grid can be proven. If your change alters the CLI or its output, update the README in the same pull request — and SKILL.md too, if install, commands, flags, exit codes or output wording changed.

## Pull requests

- Say what you changed and how you tested it, including the suite numbers before and after.

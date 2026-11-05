"""Run the API-only tool-synthesizing hybrid agent."""

from project.experiment_runner import main as run_experiment


def main(argv=None):
    return run_experiment(argv, default_strategy="hybrid")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error

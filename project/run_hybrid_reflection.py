"""Run hybrid with ordinary daily reflection."""

from project.experiment_runner import main as run_experiment


def main(argv=None):
    return run_experiment(argv, default_strategy="hybrid_reflection")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error

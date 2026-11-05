"""Run the high-level planning agent against the currently loaded SMAPI game."""

from project.experiment_runner import main as run_experiment


def main(argv=None):
    return run_experiment(argv, default_strategy="high_level")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error

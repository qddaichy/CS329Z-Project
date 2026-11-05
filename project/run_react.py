"""Run the API-only ReAct baseline against the currently loaded SMAPI game."""

from project.experiment_runner import api_key_for_config, parse_args, task_settings
from project.experiment_runner import main as run_experiment


def main(argv=None):
    return run_experiment(argv, default_strategy="react")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error

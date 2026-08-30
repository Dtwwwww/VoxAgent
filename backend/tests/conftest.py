import os
from pathlib import Path


def pytest_configure(config):
    if config.option.basetemp is not None:
        return
    configured = os.environ.get("VOXAGENT_PYTEST_TEMP")
    fallback = Path(__file__).resolve().parents[1] / ".pytest-tmp"
    config.option.basetemp = str(Path(configured).resolve() if configured else fallback)

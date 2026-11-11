import pytest

from orchestrator.config import load_config
from orchestrator.generator.pipeline import generate


# Published datasets (baseline + labelled anomalies), exactly what scripts/generate.py writes.
@pytest.fixture(scope="session")
def starter():
    return generate(load_config("starter"))


@pytest.fixture(scope="session")
def full():
    return generate(load_config("full"))


# Clean, rule-compliant baselines (same seed, no injection) - used to prove the baseline is valid.
@pytest.fixture(scope="session")
def starter_clean():
    return generate(load_config("starter"), inject=False)


@pytest.fixture(scope="session")
def full_clean():
    return generate(load_config("full"), inject=False)

import pytest

from orchestrator.config import load_config
from orchestrator.generator.pipeline import generate


@pytest.fixture(scope="session")
def starter():
    return generate(load_config("starter"))


@pytest.fixture(scope="session")
def full():
    return generate(load_config("full"))

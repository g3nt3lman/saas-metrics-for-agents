import numpy as np
import pytest

from ledgerly.config import load_config


@pytest.fixture
def small_cfg():
    return load_config(n_customers=50)


@pytest.fixture
def rng(small_cfg):
    return np.random.default_rng(small_cfg.seed)

import pytest

from ledgerly.customers import build_customers
from ledgerly.messlog import MessLog


@pytest.fixture
def customers(small_cfg, rng, faker):
    return build_customers(small_cfg, rng, faker, MessLog())


def test_customer_count(customers, small_cfg):
    assert len(customers) == small_cfg.n_customers


def test_ids_unique(customers):
    assert customers.id.is_unique
    assert customers.id.str.match(r"^cus_[0-9a-z]{14}$").all()


def test_internal_have_ledgerly_domain(customers):
    internal = customers[customers._kind == "internal"]
    assert internal.email.str.endswith("@ledgerly.io").all()
    assert not customers[customers._kind != "internal"].email.str.endswith("@ledgerly.io").any()


def test_currency_matches_country(customers, small_cfg):
    assert (customers.country.map(small_cfg.country_currency) == customers._currency).all()


def test_created_within_window(customers, small_cfg):
    d = customers.created_at.dt.date
    assert (d >= small_cfg.start).all() and (d <= small_cfg.end).all()
    assert str(customers.created_at.dt.tz) == "UTC"


def test_junk_are_soft_deleted(customers):
    assert customers[customers._kind == "junk"].is_deleted.all()
    assert not customers[customers._kind != "junk"].is_deleted.any()


def test_internal_customers_logged():
    import numpy as np
    from ledgerly.config import load_config
    from ledgerly.customers import make_faker
    cfg = load_config(n_customers=400)
    log = MessLog()
    c = build_customers(cfg, np.random.default_rng(1), make_faker(1), log)
    m = log.to_frame()
    assert set(m[m.mess_type == "internal_customers"].object_id) == set(c[c._kind == "internal"].id)
    assert len(m[m.mess_type == "internal_customers"]) > 0

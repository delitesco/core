from app.db import Session
from app.models import SLDomain, AliasOptions
from init_app import add_sl_domains
from tests.utils import create_new_user


def setup_module():
    Session.query(SLDomain).delete()
    SLDomain.create(
        domain="hidden", premium_only=False, flush=True, order=5, hidden=True
    )
    SLDomain.create(domain="free", premium_only=False, flush=True, order=4)
    SLDomain.create(domain="premium", premium_only=True, flush=True, order=3)
    Session.commit()


def teardown_module():
    Session.query(SLDomain).delete()
    add_sl_domains()


def test_get_sl_domains():
    user = create_new_user()
    domains = user.get_sl_domains()
    # Premium
    assert len(domains) == 2
    assert domains[0].domain == "premium"
    assert domains[1].domain == "free"
    assert [d.domain for d in domains] == user.available_sl_domains()
    # Free
    user.trial_end = None
    Session.flush()
    domains = user.get_sl_domains()
    assert len(domains) == 1
    assert domains[0].domain == "free"
    assert [d.domain for d in domains] == user.available_sl_domains()


def test_premium_default_domain_is_not_shown_to_free_user():
    user = create_new_user()
    user.trial_end = None
    user.default_alias_public_domain_id = (
        SLDomain.filter_by(premium_only=True).first().id
    )
    Session.flush()
    domains = user.get_sl_domains(alias_options=AliasOptions(show_sl_domains=True))
    assert [d.domain for d in domains] == ["free"]


def test_hidden_default_domain_is_not_shown():
    user = create_new_user()
    user.default_alias_public_domain_id = SLDomain.filter_by(hidden=True).first().id
    Session.flush()
    domains = user.get_sl_domains()
    assert "hidden" not in [d.domain for d in domains]

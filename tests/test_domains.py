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
    # every account is premium: premium_only domains are included, hidden ones are not
    user = create_new_user()
    domains = user.get_sl_domains()
    assert [d.domain for d in domains] == ["premium", "free"]
    assert [d.domain for d in domains] == user.available_sl_domains()


def test_default_domain_does_not_hide_other_domains():
    user = create_new_user()
    user.default_alias_public_domain_id = SLDomain.get_by(domain="free").id
    Session.flush()
    domains = user.get_sl_domains(alias_options=AliasOptions(show_sl_domains=True))
    assert [d.domain for d in domains] == ["premium", "free"]


def test_only_default_domain_when_sl_domains_are_not_shown():
    user = create_new_user()
    user.default_alias_public_domain_id = SLDomain.get_by(domain="free").id
    Session.flush()
    domains = user.get_sl_domains(alias_options=AliasOptions(show_sl_domains=False))
    assert [d.domain for d in domains] == ["free"]


def test_hidden_default_domain_is_not_shown():
    user = create_new_user()
    user.default_alias_public_domain_id = SLDomain.get_by(domain="hidden").id
    Session.flush()
    domains = user.get_sl_domains()
    assert "hidden" not in [d.domain for d in domains]
    domains = user.get_sl_domains(alias_options=AliasOptions(show_sl_domains=False))
    assert domains == []

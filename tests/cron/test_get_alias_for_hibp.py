import arrow

import cron
from app.db import Session
from app.models import (
    Alias,
)
from tests.utils import create_new_user


def test_get_alias_without_data_breach_check_is_not_checked():
    user = create_new_user()
    alias_id = Alias.create_new_random(user).id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [], alias_id, alias_id + 1)
    )
    assert len(aliases) == 0


def test_get_alias_with_null_hibp_date():
    user = create_new_user()
    user.enable_data_breach_check = True
    alias_id = Alias.create_new_random(user).id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [], alias_id, alias_id + 1)
    )
    assert alias_id == aliases[0].id


def test_get_alias_with_old_hibp_date():
    user = create_new_user()
    user.enable_data_breach_check = True
    alias = Alias.create_new_random(user)
    alias.hibp_last_check = arrow.now().shift(days=-1)
    alias_id = alias.id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [], alias_id, alias_id + 1)
    )
    assert alias_id == aliases[0].id


def test_disabled_user_is_not_checked():
    user = create_new_user()
    user.enable_data_breach_check = True
    user.disabled = True
    alias_id = Alias.create_new_random(user).id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [], alias_id, alias_id + 1)
    )
    assert len(aliases) == 0


def test_skipped_user_is_not_checked():
    user = create_new_user()
    user.enable_data_breach_check = True
    alias_id = Alias.create_new_random(user).id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [user.id], alias_id, alias_id + 1)
    )
    assert len(aliases) == 0


def test_already_checked_is_not_checked():
    user = create_new_user()
    user.enable_data_breach_check = True
    alias = Alias.create_new_random(user)
    alias.hibp_last_check = arrow.now().shift(days=1)
    alias_id = alias.id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [user.id], alias_id, alias_id + 1)
    )
    assert len(aliases) == 0


def test_outed_in_user_is_checked():
    user = create_new_user()
    user.enable_data_breach_check = True
    alias_id = Alias.create_new_random(user).id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [], alias_id, alias_id + 1)
    )
    assert len(aliases) == 1


def test_outed_out_user_is_not_checked():
    user = create_new_user()
    user.enable_data_breach_check = False
    alias_id = Alias.create_new_random(user).id
    Session.commit()
    aliases = list(
        cron.get_alias_to_check_hibp(arrow.now(), [], alias_id, alias_id + 1)
    )
    assert len(aliases) == 0

from app.alias_delete import move_alias_to_trash
from app.models import (
    UserAliasDeleteAction,
    Alias,
)
from tests.utils import create_new_user


def test_user_can_create_alias_does_not_count_trashed_ones():
    user = create_new_user(alias_delete_action=UserAliasDeleteAction.MoveToTrash)

    aliases = []
    while user.can_create_new_alias():
        aliases.append(Alias.create_new_random(user))

    assert user.can_create_new_alias() is False

    move_alias_to_trash(aliases[0], user, commit=True)
    assert user.can_create_new_alias() is True

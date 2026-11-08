from tests.utils_test_alias import alias_export


def test_export(flask_client):
    alias_export(flask_client, "api.export_aliases")

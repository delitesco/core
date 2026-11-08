import os
from io import BytesIO

import pytest

from app import config, file_storage
from tests.utils import random_token


def test_upload_and_delete():
    path = f"refused-emails/{random_token()}.eml"
    file_storage.upload_email_from_bytesio(path, BytesIO(b"hello"), "name")

    full_path = os.path.join(config.UPLOAD_DIR, path)
    with open(full_path, "rb") as f:
        assert f.read() == b"hello"

    file_storage.delete(path)
    assert not os.path.exists(full_path)
    # deleting twice is not an error
    file_storage.delete(path)


@pytest.mark.parametrize("path", ["../escape.eml", "/etc/passwd", "a/../../x.eml"])
def test_paths_cannot_escape_storage_dir(path):
    with pytest.raises(ValueError):
        file_storage.upload_email_from_bytesio(path, BytesIO(b"x"))
    with pytest.raises(ValueError):
        file_storage.delete(path)

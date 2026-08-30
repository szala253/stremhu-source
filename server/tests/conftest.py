import importlib
import sys
from unittest.mock import MagicMock

try:
    import libtorrent
except ImportError:
    mock_lt = MagicMock()
    mock_lt.torrent_handle = MagicMock
    mock_lt.session = MagicMock
    sys.modules["libtorrent"] = mock_lt


def pytest_configure(config):
    _ = config
    importlib.import_module("app.common.database")
    importlib.import_module("app.modules.roles.models")
    importlib.import_module("app.modules.preferences.models")
    importlib.import_module("app.modules.media_attributes.models")
    importlib.import_module("app.modules.torrent_files.models")
    importlib.import_module("app.modules.torrents.models")
    importlib.import_module("app.modules.users.models")
    importlib.import_module("app.modules.indexer_accounts.models")
    importlib.import_module("app.modules.indexer_definitions.models")
    importlib.import_module("app.modules.settings.models")
    importlib.import_module("app.modules.playback_histories.models")

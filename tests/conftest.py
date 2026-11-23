import pytest

from app import config, db, http, settings
from tests.fakes.world import World


@pytest.fixture
def world(tmp_path):
    config.set_data_dir(tmp_path / "data")
    config.ensure_dirs()
    db.init(tmp_path / "data" / "programarr.db")
    settings.reset_cache()
    w = World(tmp_path)
    http.set_transport(w.router)
    w.configure()
    yield w
    http.set_transport(None)
    settings.reset_cache()

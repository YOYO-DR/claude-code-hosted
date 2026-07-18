import pytest


@pytest.fixture
def fake_redis_server():
    """Un FakeServer compartido para que publisher y subscriber se vean."""
    import fakeredis

    return fakeredis.FakeServer()

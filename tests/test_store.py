from custom_components.scheduler.store import STORAGE_VERSION

def test_storage_version():
    """Ensure the storage version is correctly bumped."""
    assert STORAGE_VERSION == 4

from tempfile import TemporaryDirectory

import pytest


def pytest_configure(config):
    # App initialization during collection must not open the user's database or
    # load live credentials. Provider tests enable their own isolated settings.
    directory = TemporaryDirectory(prefix='shuce-tests-')
    environment = pytest.MonkeyPatch()
    environment.setenv('SHUCE_DATA_DIR', directory.name)
    environment.setenv('SHUCE_OFFLINE', '1')
    for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL'):
        environment.delenv(name, raising=False)
    config.add_cleanup(environment.undo)
    config.add_cleanup(directory.cleanup)

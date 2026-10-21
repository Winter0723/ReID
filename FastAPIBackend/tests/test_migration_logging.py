import logging
import pytest


@pytest.mark.parametrize('module_name', ['database', 'scenic_database'])
def test_embedded_migrations_preserve_api_and_uvicorn_logging(module_name, monkeypatch):
    import importlib
    module = importlib.import_module(module_name)
    root = logging.getLogger()
    handler = logging.NullHandler()
    monkeypatch.setattr(root, 'handlers', [handler])
    monkeypatch.setattr(root, 'level', logging.INFO)
    api = logging.getLogger('myreid.api')
    uvicorn = logging.getLogger('uvicorn.error')
    monkeypatch.setattr(api, 'disabled', False)
    monkeypatch.setattr(uvicorn, 'disabled', False)
    module.init_database()
    assert root.handlers == [handler]
    assert root.level == logging.INFO
    assert not api.disabled
    assert not uvicorn.disabled

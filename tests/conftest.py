import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(autouse=True)
def _mock_sandbox(monkeypatch):
    """Tests must never reach a real Proxmox host."""
    from pipez import config
    monkeypatch.setattr(config, "SANDBOX", "mock")
    monkeypatch.setattr(config, "DEPLOY_AUTO", False)   # deploy tests opt in

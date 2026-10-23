from unittest.mock import Mock

import pytest

from rlm.validation.limits import disk_allocation, require_capabilities
from rlm.validation.models import ValidationRequest


def test_disk_budget_includes_work_tmp_and_shm():
    for size in [16, 128, 2048, 4096]:
        request = ValidationRequest(
            workspace="/tmp/project", argv=["python", "test.py"], image="python:local", disk_mb=size
        )
        values = disk_allocation(request)
        assert values["workspace_mb"] + values["tmp_mb"] + values["shm_mb"] == size
        assert min(values.values()) > 0


def test_missing_daemon_limit_capability_fails_closed():
    client = Mock()
    values = {
        "OSType": "linux",
        "MemoryLimit": True,
        "CpuCfsQuota": True,
        "PidsLimit": True,
        "SwapLimit": True,
    }
    client.info.return_value = values
    assert require_capabilities(client)["memorylimit"] is True
    for key in ["MemoryLimit", "CpuCfsQuota", "PidsLimit", "SwapLimit"]:
        client.info.return_value = {**values, key: False}
        with pytest.raises(ValueError):
            require_capabilities(client)

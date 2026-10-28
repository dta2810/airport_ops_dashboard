"""Unmocked checks of the SDK calls the app makes: the API tests mock auth.mint, so a signature change slips past them."""

import inspect
import re
from pathlib import Path

from databricks.sdk.service.postgres import Endpoint, PostgresAPI

REQ = Path(__file__).resolve().parents[1] / "src/app/requirements.txt"


def test_sdk_pinned_exactly():
    # a >= floor silently pulls a newer SDK on the Apps build
    line = next(l for l in REQ.read_text().splitlines() if l.startswith("databricks-sdk"))
    assert re.match(r"databricks-sdk==[\d.]+", line), line


def test_installed_sdk_matches_pin():
    import databricks.sdk.version as v
    pin = re.search(r"databricks-sdk==([\d.]+)", REQ.read_text()).group(1)
    assert v.__version__ == pin, f"test venv has {v.__version__}, app pins {pin}"


def test_generate_database_credential_accepts_group_name():
    params = inspect.signature(PostgresAPI.generate_database_credential).parameters
    assert {"endpoint", "group_name"} <= set(params)


def test_get_endpoint_host_path():
    assert "name" in inspect.signature(PostgresAPI.get_endpoint).parameters
    assert "status" in Endpoint.__dataclass_fields__

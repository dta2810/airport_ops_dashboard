"""Genie access SQL generated from config/demo.yaml (no Spark). Live behaviour: tests/it_genie.py."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src" / "setup"))
from genie_access import METRIC_VIEWS, SCOPE, grant_statements, row_predicate, view_statements  # noqa: E402

ACCESS = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text())["access"]


def test_ops_control_unrestricted_and_station_scoped():
    p = row_predicate(ACCESS, "flight_state")
    assert "is_member('aops-ops-control')" in p
    assert "(is_member('aops-station-BOG') AND (origin_iata IN ('BOG') OR destination_iata IN ('BOG')))" in p


def test_sections_drop_groups_without_the_table():
    # hub connections has no gates section; stations have no connections section
    assert "aops-hub-connections" not in row_predicate(ACCESS, "gate_hour_occupancy")
    hub = row_predicate(ACCESS, "hub_connections")
    assert "aops-station-BOG" not in hub and "aops-station-PTY" not in hub


def test_no_roles_means_no_rows():
    assert row_predicate({"section_tables": ACCESS["section_tables"], "roles": []}, "flight_state") == "FALSE"


def test_quotes_are_escaped():
    acc = {"section_tables": {"x": ["flight_state"]}, "roles": [{"group": "o'neil", "airports": ["P'TY"], "sections": ["x"]}]}
    assert "is_member('o''neil')" in row_predicate(acc, "flight_state") and "'P''TY'" in row_predicate(acc, "flight_state")


def test_statements_cover_every_object():
    stmts = view_statements(ACCESS, "c.s")
    assert all(any(f"VIEW c.s.genie_{t} " in s for s in stmts) for t in SCOPE)
    assert all(any(f"VIEW c.s.{m} WITH METRICS" in s for s in stmts) for m in METRIC_VIEWS)
    assert all("EXCEPT (batch_id, last_emitted_ts, written_at)" in s for s in stmts if s.startswith("CREATE OR REPLACE VIEW c.s.genie_") and "METRICS" not in s)
    g = grant_statements("c.s", "account users")
    assert g[0] == "GRANT USE CATALOG ON CATALOG c TO `account users`" and len(g) == 2 + len(SCOPE) + len(METRIC_VIEWS)

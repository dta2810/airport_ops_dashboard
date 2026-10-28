# deploy.py ordering + idempotency guards, with the Databricks CLI faked (no workspace calls).
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deploy", ROOT / "scripts/deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

NODES = [{"node_type_id": "Standard_D4ds_v5", "num_cores": 4, "memory_mb": 16384},
         {"node_type_id": "Standard_D8ds_v5", "num_cores": 8, "memory_mb": 32768}]
VARS = {"catalog": "c", "schema": "s", "app_name": "a", "pipeline_sp_name": "sp", "pipeline_sp_app_id": "app-1",
        "gold_principal": "app-1", "lakebase_project_id": "p", "lakebase_branch_id": "production",
        "lakebase_endpoint_id": "primary", "lakebase_min_cu": "0.5", "lakebase_max_cu": "4", "lakebase_suspend": "300s"}
ROLES = [{"name": "projects/p/branches/production/roles/sp-role", "status": {"postgres_role": "app-1"}},
         {"name": "projects/p/branches/production/roles/rol-me", "status": {"postgres_role": "me@x"}}]


@pytest.fixture(autouse=True)
def bundle_dir(monkeypatch, tmp_path):
    # override files go to a temp dir, never the repo
    monkeypatch.setattr(deploy, "BUNDLE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(cmd, *, capture=False, check=True, cwd=None, mutate=True):
        seen.append(" ".join(cmd))
        if "current-user" in cmd:
            return json.dumps({"userName": "me@x"})
        if "summary" in cmd:
            return json.dumps({"resources": {"pipelines": {"bronze": {"id": "b1"}, "gold": {"id": "g1"}}}})
        if cmd[1:3] == ["groups", "create"]:
            return json.dumps({"id": "g-new"})
        if "list-node-types" in cmd:
            return json.dumps({"node_types": NODES})
        if "get-project" in cmd:
            return json.dumps({"name": "projects/p"})
        if "list-roles" in cmd:
            return json.dumps(ROLES)
        if cmd[:3] == ["databricks", "apps", "get"]:
            return json.dumps({"effective_user_api_scopes": ["postgres", "genie"], "app_status": {"state": "RUNNING"}, "url": "u"})
        return ""
    monkeypatch.setattr(deploy, "run", fake_run)
    monkeypatch.setattr(deploy.shutil, "which", lambda t: f"/bin/{t}")
    monkeypatch.setattr(deploy.Ctx, "var", lambda self, n: self.vars.get(n, deploy.load_overrides(self.target).get(n, VARS.get(n))))
    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    return seen


def _main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["deploy.py", *args, "-t", "new", "-p", "P"])
    deploy.main()


def _idx(calls, needle):
    return next(i for i, c in enumerate(calls) if needle in c)


def test_all_runs_steps_in_safe_order(monkeypatch, calls):
    _main(monkeypatch, "all")
    grant, dep = _idx(calls, "grants update catalog"), _idx(calls, "bundle deploy")
    hold = next(i for i, c in enumerate(calls) if "pipelines stop" in c and i > dep)
    assert grant < dep < hold < _idx(calls, "run setup") < _idx(calls, "run simulator_backfill") \
        < _idx(calls, "run bronze") < _idx(calls, "run gold_setup") < _idx(calls, "run dashboard")


def test_uses_target_sp_without_creating(monkeypatch, calls):
    _main(monkeypatch, "create-sp")
    assert not any("service-principals create" in c for c in calls)


def test_resume_from_step_keeps_preflight(monkeypatch, calls):
    _main(monkeypatch, "all", "--from", "setup")
    assert any("current-user me" in c for c in calls)
    assert not any("bundle deploy" in c for c in calls)


def test_verify_fails_without_postgres_scope(monkeypatch, calls):
    orig = deploy.run

    def no_scope(cmd, **kw):
        if cmd[:3] == ["databricks", "apps", "get"]:
            return json.dumps({"effective_user_api_scopes": ["sql"], "app_status": {"state": "RUNNING"}})
        return orig(cmd, **kw)
    monkeypatch.setattr(deploy, "run", no_scope)
    with pytest.raises(SystemExit, match="scopes .*missing"):
        _main(monkeypatch, "verify")


@pytest.fixture
def group_calls(monkeypatch):
    seen = []
    existing = {"aops-ops-control": {"id": "g1", "members": [{"value": "u1"}]}}

    def fake_run(cmd, *, capture=False, check=True, cwd=None, mutate=True):
        seen.append(" ".join(cmd))
        if "current-user" in cmd:
            return json.dumps({"userName": "me@x", "id": "u1"})
        if cmd[1:3] == ["groups", "list"]:
            name = cmd[cmd.index("--filter") + 1].split('"')[1]
            return json.dumps([existing[name]] if name in existing else [])
        if cmd[1:3] == ["groups", "get"]:
            return json.dumps(next(g for g in existing.values() if g["id"] == cmd[3]))
        if cmd[1:3] == ["groups", "create"]:
            return json.dumps({"id": "new-" + cmd[cmd.index("--display-name") + 1]})
        return ""
    monkeypatch.setattr(deploy, "run", fake_run)
    monkeypatch.setattr(deploy.Ctx, "var", lambda self, n: self.vars.get(n, VARS.get(n)))
    return seen


def test_groups_creates_missing_and_adds_me(monkeypatch, group_calls):
    _main(monkeypatch, "groups", "--add-me")
    created = [c for c in group_calls if "groups create" in c]
    patched = [c for c in group_calls if "groups patch" in c]
    assert len(created) == 3 and not any("aops-ops-control" in c for c in created)
    assert len(patched) == 3 and not any("patch g1" in c for c in patched)


def test_groups_does_not_add_me_by_default(monkeypatch, group_calls):
    _main(monkeypatch, "groups")
    assert not any("groups patch" in c for c in group_calls)


def test_groups_var_mismatch_fails(monkeypatch, group_calls):
    monkeypatch.setattr(deploy.Ctx, "var", lambda self, n: "other-group" if n == "access_groups" else VARS.get(n))
    with pytest.raises(SystemExit):
        _main(monkeypatch, "groups")


def test_groups_runs_before_deploy(monkeypatch, calls):
    _main(monkeypatch, "all")
    assert _idx(calls, "groups list") < _idx(calls, "bundle deploy")


# ---------- classic gold consumer ----------

@pytest.fixture
def classic_calls(monkeypatch, calls):
    orig = deploy.run

    def with_job(cmd, **kw):
        if "summary" in cmd:
            calls.append(" ".join(cmd))
            return json.dumps({"resources": {"pipelines": {"bronze": {"id": "b1"}, "gold": {"id": "g1"}},
                                             "jobs": {"gold_classic": {"id": "42"}}}})
        return orig(cmd, **kw)
    monkeypatch.setattr(deploy, "run", with_job)
    return calls


def _pause_bodies(calls):
    return [json.loads(c.split("--json ", 1)[1].rsplit(" -p ", 1)[0]) for c in calls if "jobs update" in c]


def test_start_classic_unpauses_job(monkeypatch, classic_calls):
    _main(monkeypatch, "start-classic")
    assert _pause_bodies(classic_calls) == [{"job_id": 42, "new_settings": {"continuous": {"pause_status": "UNPAUSED"}}}]


def test_stop_classic_pauses_and_cancels(monkeypatch, classic_calls):
    _main(monkeypatch, "stop-classic")
    assert _pause_bodies(classic_calls)[0]["new_settings"]["continuous"]["pause_status"] == "PAUSED"
    assert _idx(classic_calls, "jobs update") < _idx(classic_calls, "jobs cancel-all-runs --job-id 42")


def test_classic_setup_runs_job(monkeypatch, classic_calls):
    _main(monkeypatch, "classic-setup")
    assert any("bundle run gold_classic_setup" in c for c in classic_calls)


def test_stop_also_pauses_classic_when_deployed(monkeypatch, classic_calls):
    _main(monkeypatch, "stop")
    assert _idx(classic_calls, "pipelines stop g1") < _idx(classic_calls, "jobs update")


def test_classic_commands_fail_when_not_deployed(monkeypatch, calls):
    for cmd in ("start-classic", "stop-classic", "classic-setup"):
        with pytest.raises(SystemExit, match="gold_classic is not deployed"):
            _main(monkeypatch, cmd)
    assert not any("jobs update" in c or "gold_classic_setup" in c for c in calls)


def test_stop_and_all_unchanged_without_classic_job(monkeypatch, calls):
    _main(monkeypatch, "all")
    # job not in the bundle summary: no pause/cancel calls (the node-type --var is still passed)
    assert not any("jobs update" in c or "cancel-all-runs" in c or "run gold_classic" in c for c in calls)


def _n(i, cores=4, mem=16384, **kw):
    return {"node_type_id": i, "num_cores": cores, "memory_mb": mem, **kw}


def test_pick_node_type_prefers_cloud_default():
    assert deploy.pick_node_type([_n("x.4"), _n("m5d.xlarge"), _n("m5d.2xlarge", 8)]) == "m5d.xlarge"
    assert deploy.pick_node_type([_n("n2-standard-4"), _n("e2-standard-4")]) == "n2-standard-4"


def test_pick_node_type_fallback_smallest_valid():
    nodes = [_n("big", mem=32768), _n("small", mem=15360), _n("tiny", mem=8192), _n("gpu", num_gpus=1),
             _n("old", mem=14336, is_deprecated=True), _n("eight", 8, 16384)]
    assert deploy.pick_node_type(nodes) == "small"


def test_pick_node_type_none_fails():
    with pytest.raises(SystemExit):
        deploy.pick_node_type([_n("eight", 8)])


def test_deploy_passes_resolved_node_type(monkeypatch, calls):
    _main(monkeypatch, "deploy")
    dep = [c for c in calls if "bundle deploy" in c]
    assert dep and "--var=gold_classic_node_type=Standard_D4ds_v5" in dep[0]


def test_deploy_keeps_explicit_node_type(monkeypatch, calls):
    _main(monkeypatch, "deploy", "--var", "gold_classic_node_type=Standard_D8ds_v5")
    assert not any("list-node-types" in c for c in calls)
    assert "--var=gold_classic_node_type=Standard_D8ds_v5" in [c for c in calls if "bundle deploy" in c][0]


def _sp_calls(monkeypatch, entitlements):
    seen = []

    def fake_run(cmd, *, capture=False, check=True, cwd=None, mutate=True):
        seen.append(" ".join(cmd))
        if "current-user" in cmd:
            return json.dumps({"userName": "me@x"})
        if cmd[1:3] == ["service-principals", "list"]:
            return json.dumps([{"id": "42", "applicationId": "app-1", "entitlements": entitlements}])
        return ""
    monkeypatch.setattr(deploy, "run", fake_run)
    monkeypatch.setattr(deploy.shutil, "which", lambda t: f"/bin/{t}")
    monkeypatch.setattr(deploy.Ctx, "var", lambda self, n: self.vars.get(n, VARS.get(n)))
    return seen


def test_pre_grants_never_adds_cluster_create(monkeypatch):
    seen = _sp_calls(monkeypatch, [{"value": "workspace-access"}])
    _main(monkeypatch, "pre-grants")
    assert not any("service-principals patch" in c for c in seen)


def _no_bundle(monkeypatch):
    monkeypatch.setattr(deploy, "_classic_job", lambda ctx: 1)
    monkeypatch.setattr(deploy, "bundle", lambda *a, **k: "")
    monkeypatch.setattr(deploy, "stop", lambda ctx: None)


def test_classic_setup_adds_cluster_create(monkeypatch):
    seen = _sp_calls(monkeypatch, [{"value": "workspace-access"}])
    _no_bundle(monkeypatch)
    _main(monkeypatch, "classic-setup")
    patch = [c for c in seen if "service-principals patch 42" in c]
    assert patch and '"op": "add"' in patch[0] and "allow-cluster-create" in patch[0]


def test_classic_setup_skips_existing_cluster_create(monkeypatch):
    seen = _sp_calls(monkeypatch, [{"value": "allow-cluster-create"}])
    _no_bundle(monkeypatch)
    _main(monkeypatch, "classic-setup")
    assert not any("service-principals patch" in c for c in seen)


def test_destroy_removes_cluster_create(monkeypatch):
    seen = _sp_calls(monkeypatch, [{"value": "allow-cluster-create"}])
    _no_bundle(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_: "new")
    _main(monkeypatch, "destroy")
    patch = [c for c in seen if "service-principals patch 42" in c]
    assert patch and '"op": "remove"' in patch[0] and 'entitlements[value eq' in patch[0]


def test_start_skips_running_pipelines(monkeypatch, calls):
    real = deploy.run

    def with_state(cmd, **kw):
        if cmd[1:3] == ["pipelines", "get"]:
            calls.append(" ".join(cmd))
            return json.dumps({"state": "RUNNING" if cmd[3] == "b1" else "IDLE"})
        return real(cmd, **kw)
    monkeypatch.setattr(deploy, "run", with_state)
    _main(monkeypatch, "start")
    assert not any("bundle run bronze" in c for c in calls)
    assert any("bundle run gold" in c for c in calls)
    assert any("bundle run simulator_live" in c for c in calls)


@pytest.fixture
def sim_calls(monkeypatch, calls):
    orig = deploy.run

    def with_sim(cmd, **kw):
        if "summary" in cmd:
            calls.append(" ".join(cmd))
            return json.dumps({"resources": {"pipelines": {"bronze": {"id": "b1"}, "gold": {"id": "g1"}},
                                             "jobs": {"simulator_live": {"id": "77"}}}})
        return orig(cmd, **kw)
    monkeypatch.setattr(deploy, "run", with_sim)
    return calls


def test_stop_cancels_simulator_live_runs(monkeypatch, sim_calls):
    _main(monkeypatch, "stop")
    assert _idx(sim_calls, "pipelines stop g1") < _idx(sim_calls, "jobs cancel-all-runs --job-id 77")
    assert not any("jobs update" in c for c in sim_calls)


def test_stop_skips_simulator_when_not_deployed(monkeypatch, calls):
    _main(monkeypatch, "stop")
    assert not any("cancel-all-runs" in c for c in calls)


# ---------- gold identity: service_principal | deployer ----------

def _overrides(bundle_dir):
    f = bundle_dir / "new" / "variable-overrides.json"
    return json.loads(f.read_text()) if f.exists() else {}


def test_sp_mode_reuses_sp_lakebase_role(monkeypatch, calls, bundle_dir):
    _main(monkeypatch, "lakebase-role")
    assert not any("create-role" in c for c in calls)
    assert _overrides(bundle_dir) == {"lakebase_owner_role": "projects/p/branches/production/roles/sp-role",
                                      "lakebase_owner_principal": "app-1"}


def test_deployer_mode_writes_overrides_and_reuses_own_role(monkeypatch, calls, bundle_dir):
    _main(monkeypatch, "lakebase-role", "--identity", "deployer")
    o = _overrides(bundle_dir)
    assert o["gold_identity"] == "deployer" and o["gold_principal"] == "me@x"
    assert o["gold_run_as"] == {"user_name": "me@x"}
    assert o["bundle_permissions"] == [{"level": "CAN_MANAGE", "user_name": "me@x"}]
    assert o["lakebase_owner_role"] == "projects/p/branches/production/roles/rol-me"
    assert not any("create-role" in c for c in calls)


def test_deployer_mode_skips_sp_steps(monkeypatch, calls):
    _main(monkeypatch, "all", "--identity", "deployer")
    assert not any("service-principals" in c for c in calls)
    assert not any("grants update" in c for c in calls)
    assert not any("rule-sets" in c for c in calls)
    assert _idx(calls, "schemas create") < _idx(calls, "bundle deploy")


def test_identity_is_sticky_then_switches_back(monkeypatch, calls, bundle_dir):
    _main(monkeypatch, "lakebase-role", "--identity", "deployer")
    _main(monkeypatch, "pre-grants")  # no flag: stays deployer
    assert not any("grants update" in c for c in calls)
    _main(monkeypatch, "pre-grants", "--identity", "service_principal")
    assert any("grants update catalog" in c for c in calls)
    assert _overrides(bundle_dir) == {}  # identity keys + old owner role dropped


def test_lakebase_role_created_when_missing(monkeypatch, calls, bundle_dir):
    VARS_NEW = {**VARS, "gold_principal": "new-sp"}
    monkeypatch.setattr(deploy.Ctx, "var", lambda self, n: self.vars.get(n, VARS_NEW.get(n)))
    _main(monkeypatch, "lakebase-role")
    create = next(c for c in calls if "create-role" in c)
    assert "--role-id aops-new-sp" in create and '"SERVICE_PRINCIPAL"' in create and '"postgres_role": "new-sp"' in create
    assert _overrides(bundle_dir)["lakebase_owner_role"] == "projects/p/branches/production/roles/aops-new-sp"


def test_lakebase_project_created_only_when_allowed(monkeypatch, calls):
    orig = deploy.run

    def no_project(cmd, **kw):
        if "get-project" in cmd:
            return ""
        return "[]" if "list-projects" in cmd else orig(cmd, **kw)
    monkeypatch.setattr(deploy, "run", no_project)
    with pytest.raises(SystemExit, match="not found"):
        _main(monkeypatch, "lakebase-role")
    _main(monkeypatch, "lakebase-role", "--var", "lakebase_create_project=true")
    assert any("create-project p" in c for c in calls)
    assert _idx(calls, "create-project") < _idx(calls, "update-endpoint")


def test_lakebase_size_mask_and_body(monkeypatch, calls):
    _main(monkeypatch, "lakebase-size", "--var", "lakebase_min_cu=0.5", "--var", "lakebase_max_cu=4",
          "--var", "lakebase_suspend=300s")
    up = next(c for c in calls if "update-endpoint" in c)
    assert "spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu,spec.suspension" in up
    assert '"autoscaling_limit_min_cu": 0.5' in up and '"autoscaling_limit_max_cu": 4.0' in up
    assert '"suspend_timeout_duration": "300s"' in up


def test_deploy_resolves_owner_role_first(monkeypatch, calls):
    _main(monkeypatch, "deploy")
    assert _idx(calls, "list-roles") < _idx(calls, "bundle deploy")


def test_role_id_sanitized():
    assert deploy._role_id("Ops.User@example.com") == "aops-ops-user-example-com"
    assert len(deploy._role_id("x" * 100)) <= 63


def test_bad_identity_rejected(monkeypatch, calls):
    with pytest.raises(SystemExit):
        _main(monkeypatch, "preflight", "--identity", "robot")


def test_deploy_re_resolves_owner_role_when_principal_changes(monkeypatch, calls, bundle_dir):
    # override file says the owner role was resolved for another principal → lakebase-role runs again
    d = bundle_dir / "new"
    d.mkdir(parents=True, exist_ok=True)
    (d / "variable-overrides.json").write_text(json.dumps(
        {"lakebase_owner_role": "projects/p/branches/production/roles/old", "lakebase_owner_principal": "old-sp"}))
    _main(monkeypatch, "deploy")
    assert _idx(calls, "list-roles") < _idx(calls, "bundle deploy")
    o = _overrides(bundle_dir)
    assert o["lakebase_owner_principal"] == "app-1" and o["lakebase_owner_role"].endswith("/sp-role")


def test_deploy_keeps_owner_role_for_same_principal(monkeypatch, calls, bundle_dir):
    d = bundle_dir / "new"
    d.mkdir(parents=True, exist_ok=True)
    (d / "variable-overrides.json").write_text(json.dumps(
        {"lakebase_owner_role": "projects/p/branches/production/roles/sp-role", "lakebase_owner_principal": "app-1"}))
    monkeypatch.setattr(deploy.Ctx, "var", lambda self, n: self.vars.get(n, {**VARS, "lakebase_owner_role": "x"}.get(n)))
    _main(monkeypatch, "deploy")
    assert not any("list-roles" in c for c in calls)


def test_project_check_tolerates_transient_get_failure(monkeypatch, calls):
    orig = deploy.run

    def flaky(cmd, **kw):
        if "get-project" in cmd:
            return ""
        if "list-projects" in cmd:
            return json.dumps([{"name": "projects/p"}])
        return orig(cmd, **kw)
    monkeypatch.setattr(deploy, "run", flaky)
    _main(monkeypatch, "lakebase-role")
    assert not any("create-project" in c for c in calls)


def test_project_check_fails_loud_when_api_unreachable(monkeypatch, calls):
    orig = deploy.run
    monkeypatch.setattr(deploy, "run", lambda cmd, **kw: "" if "project" in " ".join(cmd) else orig(cmd, **kw))
    with pytest.raises(SystemExit, match="could not reach"):
        _main(monkeypatch, "lakebase-role")


def test_create_sp_persists_app_id_for_later_runs(monkeypatch, calls, bundle_dir):
    orig = deploy.run

    def no_sp(cmd, **kw):
        if cmd[1:3] == ["service-principals", "list"]:
            return "[]"
        if cmd[1:3] == ["service-principals", "create"]:
            return json.dumps({"applicationId": "app-new"})
        return orig(cmd, **kw)
    monkeypatch.setattr(deploy, "run", no_sp)
    monkeypatch.setitem(VARS, "pipeline_sp_app_id", "")
    _main(monkeypatch, "create-sp")
    assert deploy.load_overrides("new")["pipeline_sp_app_id"] == "app-new"


def test_classic_setup_fails_fast_without_sp(monkeypatch, calls):
    monkeypatch.setitem(VARS, "pipeline_sp_app_id", "")
    monkeypatch.setattr(deploy.Ctx, "job_id", lambda self, n: "j1")
    with pytest.raises(SystemExit, match="no pipeline SP"):
        _main(monkeypatch, "classic-setup")


def test_var_flags_are_sticky_per_target(monkeypatch, calls, bundle_dir):
    monkeypatch.setattr(sys, "argv", ["deploy.py", "preflight", "-t", "new", "-p", "P", "--var", "catalog=cat1", "--var", "genie_warehouse_id=wh1"])
    deploy.main()
    o = deploy.load_overrides("new")
    assert o["catalog"] == "cat1" and o["genie_warehouse_id"] == "wh1"

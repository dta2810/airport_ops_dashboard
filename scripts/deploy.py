#!/usr/bin/env python3
"""Deploy airport_ops_dashboard end to end from a laptop (Windows, macOS, Linux).

Needs only Python 3.10+, Node 20+ and the Databricks CLI; no bash or jq.
Every step is idempotent, so a failed run can be resumed with --from <step>.

Examples
  python scripts/deploy.py all --target new --profile MYWS --var catalog=my_catalog
  python scripts/deploy.py all --target myws --profile MYWS --from setup
  python scripts/deploy.py all --target new --profile MYWS --var catalog=c --dry-run
  python scripts/deploy.py all --target new --profile MYWS --var catalog=c --identity deployer   # no SP: gold runs as you
  python scripts/deploy.py start --target new --profile MYWS      # demo: pipelines + live emitter
  python scripts/deploy.py stop --target new --profile MYWS       # stop continuous pipelines + simulator_live runs
  python scripts/deploy.py classic-setup --target <target> --profile <profile>  # classic gold consumer targets (comparison)
  python scripts/deploy.py start-classic --target <target> --profile <profile>  # unpause the classic gold job
  python scripts/deploy.py stop-classic --target <target> --profile <profile>  # pause it and cancel its run
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src" / "app" / "frontend"
BUNDLE_DIR = ROOT / ".databricks" / "bundle"  # CLI reads <target>/variable-overrides.json (complex vars too)
STEPS = ["preflight", "create-sp", "groups", "pre-grants", "lakebase-role", "genie-objects", "build", "deploy", "hold",
         "setup", "backfill", "bronze-init", "gold-setup", "app", "verify"]
IDENTITIES = ("service_principal", "deployer")
IDENTITY_KEYS = ("gold_identity", "gold_principal", "gold_run_as", "bundle_permissions")
DRY = False


def log(msg):
    print(f"[deploy] {msg}", flush=True)


def run(cmd, *, capture=False, check=True, cwd=ROOT, mutate=True):
    # mutate=False marks read-only calls that still run in --dry-run so later steps can resolve values
    shown = " ".join(cmd)
    if DRY and mutate:
        log(f"DRY-RUN  {shown}")
        return ""
    log(shown if not capture else f"{shown}  (capture)")
    exe = shutil.which(cmd[0]) or cmd[0]  # resolves databricks.exe / npm.cmd on Windows
    res = subprocess.run([exe, *cmd[1:]], cwd=cwd, text=True, capture_output=capture)
    if check and res.returncode != 0:
        if capture:
            sys.stderr.write(res.stdout + res.stderr)
        raise SystemExit(f"[deploy] failed ({res.returncode}): {shown}")
    return res.stdout if capture else ""


def cli(args, ctx, **kw):
    return run(["databricks", *args, "-p", ctx.profile], **kw)


def bundle(args, ctx, **kw):
    return run(["databricks", "bundle", *args, "-t", ctx.target, "-p", ctx.profile, *ctx.var_flags()], **kw)


def overrides_path(target):
    return BUNDLE_DIR / target / "variable-overrides.json"


def load_overrides(target):
    f = overrides_path(target)
    return json.loads(f.read_text()) if f.exists() else {}


def save_overrides(target, updates, drop=()):
    # deploy.py-managed values; plain `databricks bundle` commands pick them up too
    data = {k: v for k, v in load_overrides(target).items() if k not in drop}
    data.update(updates)
    if DRY:
        log(f"DRY-RUN  write {overrides_path(target)}: {sorted(data)}")
        return
    f = overrides_path(target)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2) + "\n")


class Ctx:
    def __init__(self, a):
        self.target, self.profile, self.account_id = a.target, a.profile, a.account_id
        self.add_me = getattr(a, "add_me", False)  # opt-in: membership grants the union of all roles
        self.vars = dict(v.split("=", 1) for v in a.var)
        self._bundle = None
        self.identity = getattr(a, "identity", None)

    @property
    def deployer(self):
        return self.identity == "deployer"

    def sync_identity(self):
        # --identity > previous override > target var gold_identity > service_principal
        prev = load_overrides(self.target).get("gold_identity")
        mode = self.identity or self.vars.get("gold_identity") or prev or self.var("gold_identity") or IDENTITIES[0]
        if mode not in IDENTITIES:
            raise SystemExit(f"[deploy] gold identity must be one of {IDENTITIES}, got {mode!r}")
        self.identity = mode
        drop = ("lakebase_owner_role", "lakebase_owner_principal") if prev and prev != mode else ()  # owner role belongs to the old principal
        if self.deployer:
            me = json.loads(cli(["current-user", "me", "-o", "json"], self, capture=True, mutate=False))["userName"]
            self.vars.setdefault("gold_principal", me)  # also right in --dry-run, where the override file is not written
            save_overrides(self.target, {"gold_identity": "deployer", "gold_principal": me, "gold_run_as": {"user_name": me},
                                         "bundle_permissions": [{"level": "CAN_MANAGE", "user_name": me}]}, drop)
        elif prev or drop:
            save_overrides(self.target, {}, (*IDENTITY_KEYS, *drop))
        self._bundle = None
        log(f"gold identity: {mode}")

    def var_flags(self):
        return [f"--var={k}={v}" for k, v in self.vars.items()]

    def resolved(self):
        # bundle validate resolves defaults + target values + --var overrides
        if self._bundle is None:
            cmd = ["databricks", "bundle", "validate", "-o", "json", "-t", self.target, "-p", self.profile]
            res = subprocess.run([shutil.which("databricks") or "databricks", *cmd[1:], *self.var_flags()],
                                 cwd=ROOT, text=True, capture_output=True)
            if res.returncode != 0 and "pipeline_sp_app_id" in res.stderr and "pipeline_sp_app_id" not in self.vars:
                self.vars["pipeline_sp_app_id"] = "pending"  # no SP yet: create-sp fills it in
                return self.resolved()
            if res.returncode != 0:
                sys.stderr.write(res.stderr)
                raise SystemExit("[deploy] bundle validate failed; fix the variables above")
            self._bundle = json.loads(res.stdout)
        return self._bundle

    def var(self, name):
        return self.resolved()["variables"][name].get("value")

    def pipeline_id(self, key):
        out = bundle(["summary", "-o", "json"], self, capture=True, mutate=False)
        return json.loads(out)["resources"]["pipelines"][key].get("id")

    def job_id(self, key):
        # None when the job is not deployed yet
        out = bundle(["summary", "-o", "json"], self, capture=True, mutate=False)
        return (json.loads(out or "{}").get("resources", {}).get("jobs", {}).get(key) or {}).get("id")


# ---------- steps ----------

def preflight(ctx):
    for tool, why in [("databricks", "Databricks CLI"), ("node", "frontend build"), ("npm", "frontend build")]:
        if not shutil.which(tool):
            raise SystemExit(f"[deploy] missing {tool} ({why}); see docs/deployment.md#prerequisites")
    run(["databricks", "--version"], mutate=False)
    me = json.loads(cli(["current-user", "me", "-o", "json"], ctx, capture=True, mutate=False))
    log(f"signed in as {me['userName']}")
    if not ctx.var("catalog"):
        raise SystemExit("[deploy] set --var catalog=<catalog you can CREATE SCHEMA in>")
    log(f"target={ctx.target} catalog={ctx.var('catalog')} schema={ctx.var('schema')} app={ctx.var('app_name')}")


def create_sp(ctx):
    # run-as identity for the gold consumers; bundles cannot create service principals
    if ctx.deployer:
        log("deployer identity: no service principal needed")
        return
    current = ctx.var("pipeline_sp_app_id")
    if current and current != "pending":
        log(f"using pipeline SP {current} from the target/--var")
        return
    name = ctx.var("pipeline_sp_name")
    found = json.loads(cli(["service-principals", "list", "--filter", f'displayName eq "{name}"', "-o", "json"],
                           ctx, capture=True, mutate=False) or "[]")
    if found:
        app_id = found[0]["applicationId"]
        log(f"SP {name} exists: {app_id}")
    elif DRY:
        app_id = "<new-sp-app-id>"
        cli(["service-principals", "create", "--json", "..."], ctx)
    else:
        body = {"displayName": name, "active": True, "entitlements": [{"value": "workspace-access"}]}
        app_id = json.loads(cli(["service-principals", "create", "-o", "json", "--json", json.dumps(body)],
                                ctx, capture=True))["applicationId"]
        log(f"created SP {name}: {app_id} (workspace-access takes 3-5 min to propagate)")
    _grant_sp_user_role(ctx, app_id)
    ctx.vars["pipeline_sp_app_id"] = app_id
    ctx._bundle = None
    # later standalone runs (deploy, classic-setup) and plain bundle commands read it from the overrides file
    save_overrides(ctx.target, {"pipeline_sp_app_id": app_id})
    log(f"pipeline SP {app_id} saved to {overrides_path(ctx.target)}")


def access_groups():
    import yaml
    return [r["group"] for r in yaml.safe_load((ROOT / "config" / "demo.yaml").read_text())["access"]["roles"]]


def groups(ctx):
    # access groups from config/demo.yaml: create missing ones (workspace-local) and add the deployer to each
    names = access_groups()
    declared = [g.strip() for g in (ctx.var("access_groups") or "").split(",") if g.strip()]
    if declared and sorted(declared) != sorted(names):
        raise SystemExit(f"[deploy] bundle var access_groups {declared} differs from config/demo.yaml roles {names}")
    me = json.loads(cli(["current-user", "me", "-o", "json"], ctx, capture=True, mutate=False))
    for name in names:
        found = json.loads(cli(["groups", "list", "--filter", f'displayName eq "{name}"', "-o", "json"],
                               ctx, capture=True, mutate=False) or "[]")
        if found:
            gid = found[0]["id"]
            members = {m.get("value") for m in json.loads(cli(["groups", "get", gid, "-o", "json"], ctx, capture=True,
                                                              mutate=False) or "{}").get("members", [])}
            log(f"group {name} exists: {gid}")
        elif DRY:
            gid, members = f"<{name}-id>", set()
            cli(["groups", "create", "--display-name", name], ctx)
        else:
            res = run(["databricks", "groups", "create", "--display-name", name, "-o", "json", "-p", ctx.profile],
                      capture=True, check=False)
            if not res:
                raise SystemExit(f"[deploy] could not create group {name} (workspace admin needed): ask an admin to run "
                                 f"`databricks groups create --display-name {name}`, then resume with --from groups")
            gid, members = json.loads(res)["id"], set()
            log(f"created group {name}: {gid}")
        if ctx.add_me and me.get("id") not in members:
            patch = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                     "Operations": [{"op": "add", "path": "members", "value": [{"value": me.get("id")}]}]}
            cli(["groups", "patch", gid, "--json", json.dumps(patch)], ctx, capture=True)
            log(f"added {me['userName']} to {name}")
    if not ctx.add_me:
        log("groups created; you were not added. Use --add-me to test every role yourself, "
            "or add demo users to the groups (Settings > Identity and access > Groups)")


def _grant_sp_user_role(ctx, app_id):
    # run_as needs the deployer to hold Service principal: User (creator only gets Manager)
    acct = ctx.account_id or _profile_account_id(ctx)
    if not acct:
        log("no account id: add yourself as 'Service principal: User' on the SP in the UI (keep Manager)")
        return
    me = json.loads(cli(["current-user", "me", "-o", "json"], ctx, capture=True, mutate=False))["userName"]
    rs = f"accounts/{acct}/servicePrincipals/{app_id}/ruleSets/default"
    if DRY:
        cli(["api", "put", "/api/2.0/preview/accounts/access-control/rule-sets", "--json", "..."], ctx)
        return
    cur = json.loads(cli(["api", "get", f"/api/2.0/preview/accounts/access-control/rule-sets?name={rs}&etag="],
                         ctx, capture=True))
    rules = {r["role"]: set(r.get("principals", [])) for r in cur.get("grant_rules", [])}
    rules.setdefault("roles/servicePrincipal.user", set()).add(f"users/{me}")
    body = {"name": rs, "rule_set": {"name": rs, "etag": cur["etag"],
            "grant_rules": [{"role": k, "principals": sorted(v)} for k, v in rules.items()]}}
    cli(["api", "put", "/api/2.0/preview/accounts/access-control/rule-sets", "--json", json.dumps(body)], ctx, capture=True)
    log(f"{me} holds Service principal: User on {app_id}")


def _profile_account_id(ctx):
    try:
        d = json.loads(cli(["auth", "describe", "-o", "json"], ctx, capture=True, check=False, mutate=False) or "{}")
        return d.get("details", {}).get("configuration", {}).get("account_id", {}).get("value")
    except json.JSONDecodeError:
        return None


def pre_grants(ctx):
    # the gold pipeline runs as the SP: UC access must exist before the pipeline is created
    cat, sch, sp = ctx.var("catalog"), ctx.var("schema"), ctx.var("pipeline_sp_app_id")
    cli(["schemas", "create", sch, cat], ctx, check=False, capture=True)
    if ctx.deployer:
        log("deployer identity: you own the schema; no SP grants or entitlements")
        return
    cli(["grants", "update", "catalog", cat, "--json",
         json.dumps({"changes": [{"principal": sp, "add": ["USE_CATALOG"]}]})], ctx, capture=True)
    cli(["grants", "update", "schema", f"{cat}.{sch}", "--json",
         json.dumps({"changes": [{"principal": sp, "add": ["USE_SCHEMA", "CREATE_TABLE", "SELECT"]}]})], ctx, capture=True)


def _sp_cluster_create(ctx, app_id, add=True):
    # gold_classic runs as the SP on a job cluster: granted by classic-setup only, removed by destroy
    found = json.loads(cli(["service-principals", "list", "--filter", f'applicationId eq "{app_id}"', "-o", "json"],
                           ctx, capture=True, mutate=False) or "[]")
    if not found:
        log(f"SP {app_id} not found; skipping allow-cluster-create")
        return
    has = any(e.get("value") == "allow-cluster-create" for e in found[0].get("entitlements", []))
    if has == add:
        return
    op = ({"op": "add", "path": "entitlements", "value": [{"value": "allow-cluster-create"}]} if add else
          {"op": "remove", "path": 'entitlements[value eq "allow-cluster-create"]'})
    patch = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": [op]}
    cli(["service-principals", "patch", found[0]["id"], "--json", json.dumps(patch)], ctx, capture=True)
    log(f"SP {app_id}: allow-cluster-create {'added (classic gold job cluster)' if add else 'removed'}")


def _role_id(principal):
    # Lakebase role ids: lowercase letters, digits, hyphens, <= 63 chars
    rid = "".join(ch if ch.isalnum() else "-" for ch in principal.lower()).strip("-")
    return f"aops-{rid}"[:63].rstrip("-")


def _project_exists(ctx, project):
    # get-project also fails on transient CLI/auth errors: confirm a miss against list-projects
    if cli(["postgres", "get-project", f"projects/{project}", "-o", "json"], ctx, capture=True, check=False,
           mutate=False):
        return True
    out = cli(["postgres", "list-projects", "-o", "json"], ctx, capture=True, check=False, mutate=False)
    if not out:
        raise SystemExit("[deploy] could not reach the Lakebase API (list-projects failed); check auth and rerun")
    found = json.loads(out)
    found = found.get("projects", []) if isinstance(found, dict) else found
    return any(p.get("name") == f"projects/{project}" for p in found)


def lakebase_role(ctx):
    # owner role of the gold database = the gold principal's Lakebase role (found or created)
    project, branch = ctx.var("lakebase_project_id"), ctx.var("lakebase_branch_id")
    if not _project_exists(ctx, project):
        if str(ctx.var("lakebase_create_project")).lower() != "true":
            raise SystemExit(f"[deploy] Lakebase project {project} not found; create it or use target new")
        body = {"spec": {"display_name": "Airport Ops Dashboard", "pg_version": 17}}
        cli(["postgres", "create-project", project, "--json", json.dumps(body)], ctx, capture=True)
        log(f"created Lakebase project {project}")
        lakebase_size(ctx)  # platform default is 1-1 CU, suspend 24 h
    principal = ctx.var("gold_principal")
    if not principal or principal == "pending":
        raise SystemExit("[deploy] no gold principal: run create-sp first, or use --identity deployer")
    parent = f"projects/{project}/branches/{branch}"
    out = cli(["postgres", "list-roles", parent, "-o", "json"], ctx, capture=True, mutate=False, check=False)
    roles = json.loads(out or "[]")
    roles = roles.get("roles", []) if isinstance(roles, dict) else roles
    found = [r for r in roles if (r.get("status") or {}).get("postgres_role") == principal]
    if found:
        name = found[0]["name"]
        log(f"Lakebase role for {principal}: {name}")
    else:
        rid = _role_id(principal)
        kind = "USER" if ctx.deployer else "SERVICE_PRINCIPAL"
        spec = {"spec": {"identity_type": kind, "postgres_role": principal, "auth_method": "LAKEBASE_OAUTH_V1"}}
        cli(["postgres", "create-role", parent, "--role-id", rid, "--json", json.dumps(spec)], ctx, capture=True)
        name = f"{parent}/roles/{rid}"
        log(f"created Lakebase role {name} ({kind} {principal})")
    ctx.vars["lakebase_owner_role"] = name
    save_overrides(ctx.target, {"lakebase_owner_role": name, "lakebase_owner_principal": principal})
    ctx._bundle = None


def lakebase_size(ctx):
    # autoscaling range + auto-suspend on the gold endpoint (mask for suspend is spec.suspension)
    ep = (f"projects/{ctx.var('lakebase_project_id')}/branches/{ctx.var('lakebase_branch_id')}"
          f"/endpoints/{ctx.var('lakebase_endpoint_id')}")
    lo, hi, idle = float(ctx.var("lakebase_min_cu")), float(ctx.var("lakebase_max_cu")), ctx.var("lakebase_suspend")
    body = {"spec": {"autoscaling_limit_min_cu": lo, "autoscaling_limit_max_cu": hi, "suspend_timeout_duration": idle}}
    cli(["postgres", "update-endpoint", ep, "spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu,spec.suspension",
         "--json", json.dumps(body)], ctx, capture=True)
    log(f"Lakebase endpoint {ep}: {lo}-{hi} CU, suspend after {idle}")


def sql(stmt, ctx):
    body = {"warehouse_id": ctx.var("genie_warehouse_id"), "statement": stmt, "wait_timeout": "50s"}
    out = cli(["api", "post", "/api/2.0/sql/statements", "--json", json.dumps(body)], ctx, capture=True)
    st = json.loads(out)["status"] if out else {"state": "SUCCEEDED"}
    if st["state"] != "SUCCEEDED":
        raise SystemExit(f"[deploy] SQL {st['state']}: {st.get('error', {}).get('message')}\n{stmt[:300]}")


def genie_objects(ctx):
    # the Genie space is rejected if its views do not exist, so create them before `bundle deploy`
    sys.path.insert(0, str(ROOT / "src" / "setup"))
    import yaml
    from genie_access import grant_statements, view_statements
    uc = f"{ctx.var('catalog')}.{ctx.var('schema')}"
    ddl = (ROOT / "src" / "sql" / "delta_replicas.sql").read_text().format(schema=uc)
    access = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text())["access"]
    for stmt in [s for s in ddl.split(";") if "CREATE TABLE" in s] + view_statements(access, uc) \
            + grant_statements(uc, ctx.var("genie_reader")):
        sql(stmt, ctx)


def build(ctx):
    run(["npm", "ci", "--silent"], cwd=FRONTEND)
    run(["npm", "run", "build", "--silent"], cwd=FRONTEND)


PREFERRED_NODES = ["Standard_D4ds_v5", "m5d.xlarge", "n2-standard-4"]  # Azure, AWS, GCP: 4 cores, 16 GB, local disk


def pick_node_type(node_types):
    # 4-core general-purpose node of this workspace's cloud: preferred name first, else smallest memory >= 14 GB
    ok = [n for n in node_types if n.get("num_cores") == 4 and n.get("memory_mb", 0) >= 14000
          and not n.get("is_deprecated") and not n.get("is_hidden") and not n.get("num_gpus")]
    ids = {n["node_type_id"] for n in ok}
    for pref in PREFERRED_NODES:
        if pref in ids:
            return pref
    if not ok:
        raise SystemExit("[deploy] no 4-core node type found; pass --var gold_classic_node_type=<node type>")
    return min(ok, key=lambda n: (n["memory_mb"], n["node_type_id"]))["node_type_id"]


def classic_node_type(ctx):
    if ctx.vars.get("gold_classic_node_type") or ctx.var("gold_classic_node_type"):
        return
    out = cli(["clusters", "list-node-types", "-o", "json"], ctx, capture=True, mutate=False)
    ctx.vars["gold_classic_node_type"] = pick_node_type(json.loads(out or "{}").get("node_types", []))
    ctx._bundle = None
    log(f"gold_classic_node_type={ctx.vars['gold_classic_node_type']} (override with --var)")


def deploy(ctx):
    owner_for = load_overrides(ctx.target).get("lakebase_owner_principal")
    stale = owner_for is not None and owner_for != ctx.var("gold_principal")  # principal changed since lakebase-role
    if stale or not (ctx.vars.get("lakebase_owner_role") or ctx.var("lakebase_owner_role")):
        ctx.vars.pop("lakebase_owner_role", None)
        lakebase_role(ctx)
    classic_node_type(ctx)
    bundle(["validate"], ctx, mutate=False)
    bundle(["deploy"], ctx)


def hold(ctx):
    # continuous pipelines start on create; keep them stopped until the data is in place
    stop(ctx)


def setup(ctx):
    bundle(["run", "setup"], ctx)  # UC schema/volume/grants, Lakebase DDL, group roles + RLS


def backfill(ctx):
    bundle(["run", "simulator_backfill"], ctx)


def bronze_init(ctx):
    # bronze's first run creates the table the gold views read
    bundle(["run", "bronze", "--no-wait"], ctx)
    table = f"{ctx.var('catalog')}.{ctx.var('schema')}.bronze_flight_milestones"
    if DRY:
        log(f"DRY-RUN  wait for {table}")
        return
    for _ in range(60):
        if subprocess.run([shutil.which("databricks"), "tables", "get", table, "-p", ctx.profile],
                          capture_output=True).returncode == 0:
            log(f"{table} exists")
            break
        time.sleep(15)
    else:
        raise SystemExit(f"[deploy] {table} not created after 15 min; check the bronze pipeline event log")


def gold_setup(ctx):
    bundle(["run", "gold_setup"], ctx)  # views, Delta replicas, gold_params
    stop(ctx)


def app(ctx):
    bundle(["run", "dashboard"], ctx)


def verify(ctx):
    # bundle deploys are full replacement: fail if the OBO scope did not survive
    name = ctx.var("app_name")
    if DRY:
        cli(["apps", "get", name], ctx)
        return
    a = json.loads(cli(["apps", "get", name, "-o", "json"], ctx, capture=True))
    scopes = a.get("effective_user_api_scopes") or []
    state = (a.get("app_status") or {}).get("state")
    missing = {"postgres", "genie"} - set(scopes)
    if missing:
        raise SystemExit(f"[deploy] FATAL: scopes {sorted(missing)} missing on {name}: {scopes}")
    if state != "RUNNING":
        raise SystemExit(f"[deploy] app {name} is {state}: run `databricks apps logs {name} -p {ctx.profile}`")
    log(f"app {name} RUNNING at {a.get('url')} with scopes {scopes}")
    log("next: open the app URL in a browser (accept the OAuth consent), then `deploy.py start` for live data")


def _pipeline_running(ctx, key):
    # a second `bundle run` on a running continuous pipeline fails ("active update already exists")
    pid = ctx.pipeline_id(key)
    if not pid:
        return False
    out = cli(["pipelines", "get", pid, "-o", "json"], ctx, capture=True, mutate=False, check=False)
    return (json.loads(out or "{}").get("state") or "") == "RUNNING"


def start(ctx):
    for key in ("bronze", "gold"):
        if _pipeline_running(ctx, key):
            log(f"{key} already running; skipped")
        else:
            bundle(["run", key, "--no-wait"], ctx)
    bundle(["run", "simulator_live", "--no-wait"], ctx)
    log("continuous pipelines are running and billing; run `deploy.py stop` when done")


def stop(ctx):
    for key in ("bronze", "gold"):
        pid = ctx.pipeline_id(key) if not DRY else f"<{key}-id>"
        if pid:
            cli(["pipelines", "stop", pid], ctx, check=False, capture=True)
    # simulator_live runs ~1 h and keeps landing files; cancel its active runs (no-op if none)
    sim = ctx.job_id("simulator_live")
    if sim:
        cli(["jobs", "cancel-all-runs", "--job-id", str(sim)], ctx, check=False, capture=True)
    if ctx.job_id("gold_classic"):
        stop_classic(ctx)


# ---------- classic gold consumer (comparison, docs/deployment.md) ----------

def _classic_job(ctx):
    jid = ctx.job_id("gold_classic")
    if not jid:
        raise SystemExit(f"[deploy] job gold_classic is not deployed for target {ctx.target}; run `deploy` first")
    return jid


def _set_pause(ctx, jid, status):
    body = {"job_id": int(jid), "new_settings": {"continuous": {"pause_status": status}}}
    cli(["jobs", "update", "--json", json.dumps(body)], ctx, capture=True)


def classic_setup(ctx):
    _classic_job(ctx)
    if not ctx.deployer:
        sp = ctx.var("pipeline_sp_app_id")
        if not sp or sp == "pending":
            raise SystemExit("[deploy] no pipeline SP for this target: run `deploy.py create-sp` "
                             "or pass --var pipeline_sp_app_id=<app id>")
        _sp_cluster_create(ctx, sp)
    bundle(["run", "gold_classic_setup"], ctx)  # replica schema + chk volume + grants, Lakebase classic schema


def start_classic(ctx):
    _set_pause(ctx, _classic_job(ctx), "UNPAUSED")
    log("gold_classic UNPAUSED: its job cluster bills while running; `deploy.py stop-classic` when done "
        "(a later `bundle deploy` re-applies PAUSED)")


def stop_classic(ctx):
    jid = _classic_job(ctx)
    _set_pause(ctx, jid, "PAUSED")
    cli(["jobs", "cancel-all-runs", "--job-id", str(jid)], ctx, check=False, capture=True)
    log("gold_classic PAUSED")


def destroy(ctx):
    stop(ctx)
    if input(f"Type the target name ({ctx.target}) to destroy its bundle resources: ") != ctx.target:
        raise SystemExit("aborted")
    bundle(["destroy", "--auto-approve"], ctx)
    if not ctx.deployer:
        _sp_cluster_create(ctx, ctx.var("pipeline_sp_app_id"), add=False)
    log("left in place on purpose: the pipeline SP, workspace groups (and your membership if you used --add-me), "
        "UC grants on the catalog, Lakebase project + roles")


ACTIONS = {"preflight": preflight, "create-sp": create_sp, "groups": groups, "pre-grants": pre_grants,
           "lakebase-role": lakebase_role, "genie-objects": genie_objects, "build": build,
           "deploy": deploy, "hold": hold, "setup": setup, "backfill": backfill, "bronze-init": bronze_init,
           "gold-setup": gold_setup, "app": app, "verify": verify}
EXTRA = {"start": start, "stop": stop, "destroy": destroy, "lakebase-size": lakebase_size,
         "classic-setup": classic_setup, "start-classic": start_classic, "stop-classic": stop_classic}


def main():
    global DRY
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["all", *STEPS, *EXTRA])
    p.add_argument("--target", "-t", required=True, help="bundle target: new | reuse | <your own>")
    p.add_argument("--profile", "-p", required=True, help="Databricks CLI profile (databricks auth login)")
    p.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="bundle variable override (repeatable)")
    p.add_argument("--account-id", help="account id for the SP User role (defaults to the profile's)")
    p.add_argument("--from", dest="from_step", choices=STEPS, help="with 'all': resume from this step")
    p.add_argument("--dry-run", action="store_true", help="print mutating commands instead of running them")
    p.add_argument("--identity", choices=IDENTITIES,
                   help="who gold runs as: service_principal (default) or deployer (no SP); sticky per target")
    p.add_argument("--add-me", action="store_true", help="groups step: also add yourself to every access group (demo)")
    p.add_argument("--no-add-me", action="store_true", help=argparse.SUPPRESS)  # old flag; not adding is the default now
    a = p.parse_args()
    DRY = a.dry_run
    ctx = Ctx(a)
    if ctx.vars:  # --var values are sticky per target: later start/stop/classic-* runs need not repeat them
        save_overrides(ctx.target, dict(ctx.vars))
    ctx.sync_identity()
    if a.command in EXTRA:
        EXTRA[a.command](ctx)
        return
    steps = STEPS if a.command == "all" else [a.command]
    if a.from_step:
        steps = STEPS[STEPS.index(a.from_step):]
    if "preflight" not in steps:
        steps = ["preflight", *steps]
    for s in steps:
        log(f"== {s}")
        ACTIONS[s](ctx)
    log("done")


if __name__ == "__main__":
    main()

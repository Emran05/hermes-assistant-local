#!/usr/bin/env python3
"""Round 3, cluster a-roster: the curated roster additions and the new optional
roster fields, threaded through every dashboard place that resolves a model.

Design: docs/plans/abliterated-models-2026-10.md ("What adding them takes")
and docs/plans/model-advisor-2026-10.md §6 (catalog). Owner decisions: the
uncensored builds are opt-in alternatives only — never the default, never
recommended by onboarding — and everything new is unverified until drilled.

Loads the REAL dashboard/server.py (and with it every aux module, aux_config
and aux_onboarding included) against a THROWAWAY HOME with a fake Hugging Face
hub cache. Nothing is downloaded (the snapshot_download subprocess runner is a
recorder), no model is woken (no switch/restart path is taken), no port is
contacted (the offline guard refuses the model ports anyway).

Covers
  1  the curated seeds: full shape, never default, never pickles/code, no
     drafter, sizes recorded, uncensored never in the onboarding catalog/tier
  2  _roster_subfolder / _roster_sampling validation matrices
  3  _model_registry: seed-only facts never stored, independent copies,
     ordered migration (new seeds after the 9B lane; the old single-seed rule
     unchanged), backfill of the new keys, user edits win
  4  _write_template_args -> server-backend: model id, model_subfolder (+ forced
     hf_offline), validated sampling; invalid subfolder; stock entries clean
  5  _model_downloaded / switch_model judge <snapshot>/<model_subfolder>
  6  _draft_model_path refuses a "../" draft_subfolder
  7  download_model scopes the pull (roster allow_patterns, or <sub>/* derived),
     keeps the never-code/never-pickle list, refuses an invalid subfolder
  8  _catalog_dl_gb / _model_download_gb: seed sizes, subfolder-only measuring
  9  models_payload badges: uncensored sticky to the seed, verified/sampling/
     model_subfolder/license normalized
 10  aux_config roster import/export of the new fields
 11  curation: every new id is curated (add_model needs no confirm)
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


TMP = tempfile.mkdtemp(prefix="hermes-r3-roster-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_r3_roster",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_r3_roster"] = srv
spec.loader.exec_module(srv)
print("server.py loaded (HOME=%s)" % TMP)
assert srv.HOME == TMP, srv.HOME
# models_payload() asks whether the background lane is up; answer "down" here
# rather than let it try 127.0.0.1:8081 (the offline guard would refuse it).
srv._bg_probe = lambda: False

P27 = "mlx-community/Qwen3.8-27B-4bit"
ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
Q9 = "mlx-community/Qwen3.5-9B-4bit"
MOE = "mlx-community/Qwen3.6-35B-A3B-4bit"
POCKET = "PocketAiHub/Qwen3.8-27B-Abliterated-MLX"
HUIHUI = "AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit"
HERETIC = "Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit"
BG9 = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"
DSV4 = "digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit"    # 2026-11-12
NEW = [MOE, POCKET, HUIHUI, HERETIC, BG9, DSV4]
UNCENSORED = {ORCA, POCKET, HUIHUI, HERETIC, BG9, DSV4}
SEEDS = {m["id"]: m for m in srv._SEED_MODELS}


def models_file():
    return srv.MODELS_FILE


def write_models(rows):
    with open(models_file(), "w") as f:
        json.dump({"models": rows}, f)


def read_models():
    with open(models_file()) as f:
        return json.load(f)["models"]


def reset_models():
    try:
        os.remove(models_file())
    except FileNotFoundError:
        pass


HUB = os.path.join(TMP, ".cache", "huggingface", "hub")


def fake_snapshot(mid, layout, sha="abc123"):
    """A hub-cache entry: refs/main -> snapshots/<sha>/ holding `layout`
    ({relative path: text}). Returns the snapshot dir."""
    base = os.path.join(HUB, "models--" + mid.replace("/", "--"))
    snap = os.path.join(base, "snapshots", sha)
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(os.path.join(base, "refs"))
    with open(os.path.join(base, "refs", "main"), "w") as f:
        f.write(sha)
    for rel, text in layout.items():
        p = os.path.join(snap, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    os.makedirs(snap, exist_ok=True)
    return snap


def model_dir(prefix, shards=("model-00001-of-00002.safetensors",
                              "model-00002-of-00002.safetensors"), drop=()):
    pre = (prefix + "/") if prefix else ""
    lay = {pre + "config.json": "{}",
           pre + "model.safetensors.index.json":
               json.dumps({"weight_map": {"w%d" % i: s for i, s in enumerate(shards)}})}
    for s in shards:
        if s not in drop:
            lay[pre + s] = "x"
    return lay


# --------------------------------------------------------------------------
def test_seeds():
    print("\n1. the curated seeds")
    for mid in NEW:
        check("%s is a seed" % mid, mid in SEEDS)
    for mid in NEW:
        m = SEEDS.get(mid) or {}
        check("%s: full mlx_vlm shape (backend, thinking, template_args off, ctx)" % mid,
              m.get("backend") == "mlx_vlm" and m.get("thinking") is True
              and m.get("template_args") == {"enable_thinking": False}
              and m.get("ctx") == 262144, m)
        check("%s: ram is a number _model_fit can judge" % mid,
              isinstance(m.get("ram"), int) and 5 <= m["ram"] <= 30, m.get("ram"))
        check("%s: unverified until drilled" % mid, m.get("verified") is False, m.get("verified"))
        check("%s: uncensored flag matches the curation" % mid,
              m.get("uncensored") is (mid in UNCENSORED), m.get("uncensored"))
        check("%s: never trust_remote_code" % mid, m.get("trust_remote_code") is False)
        check("%s: no drafter (an MTP pairing is unmeasured)" % mid,
              not any(m.get(k) for k in ("draft_model", "draft_subfolder")), m)
        check("%s: verified download size recorded" % mid,
              isinstance(m.get("size_gb"), float) and m["size_gb"] > 1, m.get("size_gb"))
        pats = m.get("allow_patterns") or []
        check("%s: allow_patterns scope the pull to weights/JSON/template/tokenizer" % mid,
              pats and all(p.rsplit("*", 1)[-1] in (".safetensors", ".json", ".jinja",
                                                     ".txt", ".model") for p in pats), pats)
        ign = srv._dl_ignore_patterns(m, m.get("ignore_patterns"))
        check("%s: the download still ignores *.py and every pickle format" % mid,
              all(p in ign for p in ("*.py", "*.pyc", "*.bin", "*.pt", "*.pth",
                                     "*.ckpt", "*.pkl", "*.pickle")), ign)
        check("%s: label/note fit the config cleaner's limits" % mid,
              len(m.get("label", "")) <= 64 and len(m.get("note", "")) <= 120)
        check("%s: never the default model" % mid,
              mid not in (srv.DEFAULT_MODEL, srv.DEFAULT_BG_MODEL))
    check("PocketAiHub: weights in the 4bit/ subfolder, hf_offline, scoped to 4bit/",
          SEEDS[POCKET].get("model_subfolder") == "4bit" and SEEDS[POCKET].get("hf_offline") is True
          and all(p.startswith("4bit/") for p in SEEDS[POCKET]["allow_patterns"]))
    check("vanch007 9B is a background-lane alternative",
          SEEDS[BG9].get("role") == "background")
    check("vanch007 records that its repo states no licence", SEEDS[BG9].get("license") is None
          and "license" in SEEDS[BG9])
    check("the existing seeds gained verified/uncensored truthfully",
          SEEDS[P27].get("verified") is True and SEEDS[Q9].get("verified") is True
          and SEEDS[ORCA].get("verified") is False and SEEDS[ORCA].get("uncensored") is True
          and SEEDS[P27].get("uncensored") is False)
    check("seed ids are unique", len(SEEDS) == len(srv._SEED_MODELS))
    check("no shipped seed carries sampling defaults (no author of these repos "
          "publishes any; the field is for the owner's drill to set)",
          not any(m.get("sampling") for m in srv._SEED_MODELS))
    # onboarding never recommends an uncensored build
    cat = getattr(srv, "_ONB_BY_ID", {}) or {}
    check("no uncensored id in the onboarding catalog", not (UNCENSORED & set(cat)),
          sorted(UNCENSORED & set(cat)))
    for gb in (8, 16, 24, 36, 48, 64, 128, 192):
        plan = srv._onb_tier(gb)
        ids = {plan.get("primary"), plan.get("background")}
        alt = plan.get("alt") or {}
        ids |= {alt.get("primary"), alt.get("background")}
        check("_onb_tier(%d) never names an uncensored build" % gb,
              not (ids & UNCENSORED), ids)


def test_validators():
    print("\n2. _roster_subfolder / _roster_sampling")
    sf = srv._roster_subfolder
    for v, want in (("4bit", "4bit"), ("4bit/", "4bit"), ("/4bit", "4bit"),
                    ("a/b", "a/b"), ("a.b_c-d", "a.b_c-d"),
                    ("../x", ""), ("a/../b", ""), (".", ""), ("..", ""), ("a/./b", ""),
                    ("a\\b", ""), ("a b", ""), ("", ""), ("   ", ""), (None, ""),
                    (4, ""), (["4bit"], ""), ("x" * 65, ""), ("a/b/c/d/e", ""),
                    ("a//b", "")):
        check("_roster_subfolder(%r) == %r" % (v, want), sf(v) == want, sf(v))
    smp = srv._roster_sampling
    got = smp({"repetition_penalty": 1.15, "top_k": 20, "temperature": 0,
               "top_p": 0.95, "min_p": 0.0, "presence_penalty": -1,
               "frequency_penalty": 0.5, "repetition_context_size": 64})
    check("valid sampling kept, floats float / ints int",
          got == {"repetition_penalty": 1.15, "top_k": 20, "temperature": 0.0,
                  "top_p": 0.95, "min_p": 0.0, "presence_penalty": -1.0,
                  "frequency_penalty": 0.5, "repetition_context_size": 64}
          and isinstance(got["temperature"], float) and isinstance(got["top_k"], int), got)
    check("top_k 20.0 is accepted as 20", smp({"top_k": 20.0}) == {"top_k": 20})
    for bad in ({"top_k": 20.5}, {"repetition_penalty": True}, {"repetition_penalty": "1.15"},
                {"repetition_penalty": 2.5}, {"repetition_penalty": 0.9},
                {"temperature": float("nan")}, {"temperature": float("inf")},
                {"top_p": 1.5}, {"seed": 3}, {"logit_bias": {"1": 2}}, {"top_k": -1}):
        check("sampling %r dropped (never clamped)" % (bad,), smp(bad) == {}, smp(bad))
    for notdict in (None, [], "x", 1.15):
        check("sampling %r -> {}" % (notdict,), smp(notdict) == {})


def test_registry():
    print("\n3. _model_registry")
    reset_models()
    rows = srv._model_registry()
    check("fresh install: the seed roster, in seed order",
          [m["id"] for m in rows] == [m["id"] for m in srv._SEED_MODELS])
    stored = read_models()
    check("seed-only facts (size_gb/draft_size_gb/trust_remote_code) are never stored",
          not any(k in m for m in stored for k in srv._SEED_ONLY_KEYS), stored[:2])
    check("...but the new roster keys are", all("verified" in m and "uncensored" in m
                                                  for m in stored))
    rows[0]["template_args"]["enable_thinking"] = True
    rows[0]["label"] = "edited"
    check("returned rows are independent of _SEED_MODELS (nested dicts too)",
          SEEDS[P27]["template_args"] == {"enable_thinking": False}
          and SEEDS[P27]["label"] == "Qwen3.8-27B")

    # the unreadable-store view is a copy too
    os.chmod(models_file(), 0)
    try:
        if os.access(models_file(), os.R_OK):
            print("  SKIP unreadable-store case: running as root")
        else:
            view = srv._model_registry()
            view[0]["label"] = "x"
            check("unreadable models.json: seed view is a stripped copy",
                  SEEDS[P27]["label"] == "Qwen3.8-27B"
                  and not any(k in m for m in view for k in srv._SEED_ONLY_KEYS))
    finally:
        os.chmod(models_file(), 0o600)

    # migration of a pre-round-3 roster with a custom model at the end
    old = [{"id": P27, "label": "Qwen3.8-27B", "ram": 19},
           {"id": ORCA, "label": "Qwen3.8-27B Uncensored", "ram": 19},
           {"id": Q9, "label": "Qwen3.5-9B", "ram": 7, "role": "background"},
           {"id": "me/custom-7b", "label": "Mine", "ram": 5}]
    write_models(old)
    ids = [m["id"] for m in srv._model_registry()]
    check("migration: round-3 seeds land after the 9B lane, in seed order, "
          "before the owner's own model",
          ids == [P27, ORCA, Q9, MOE, POCKET, HUIHUI, HERETIC, BG9, DSV4, "me/custom-7b"], ids)
    # the old single-seed rule is unchanged
    write_models([{"id": P27, "label": "x"}, {"id": Q9, "label": "y"}])
    ids = [m["id"] for m in srv._model_registry()]
    check("migration: a seed whose predecessor is present goes right after it "
          "(orca after the 27B, as before)", ids[:3] == [P27, ORCA, Q9], ids)
    write_models([{"id": "me/only", "label": "z"}])
    ids = [m["id"] for m in srv._model_registry()]
    check("migration: with no seed present, the seeds go first in seed order (old rule)",
          ids == [m["id"] for m in srv._SEED_MODELS] + ["me/only"], ids)

    # backfill + user edits win
    write_models([{"id": P27, "label": "Mine27"},
                  {"id": POCKET, "label": "Pocket6", "model_subfolder": "6bit",
                   "uncensored": False, "verified": True}])
    reg = {m["id"]: m for m in srv._model_registry()}
    p = reg[POCKET]
    check("backfill: absent keys arrive (backend, allow_patterns, license)",
          p.get("backend") == "mlx_vlm" and p.get("allow_patterns") and p.get("license"), p)
    check("backfill: the owner's model_subfolder/verified/uncensored edits win",
          p["model_subfolder"] == "6bit" and p["verified"] is True and p["uncensored"] is False, p)
    check("backfill: seed-only facts are not backfilled",
          not any(k in p for k in srv._SEED_ONLY_KEYS), p)
    check("backfill: the 27B gains verified/uncensored/license, keeps its label",
          reg[P27].get("verified") is True and reg[P27].get("label") == "Mine27", reg[P27])
    check("_model_subfolder_of reads the LIVE roster (owner's 6bit wins)",
          srv._model_subfolder_of(POCKET) == "6bit")
    reset_models()
    check("_model_subfolder_of falls back to the seed", srv._model_subfolder_of(POCKET) == "4bit")


def test_backend_file():
    print("\n4. _write_template_args -> server-backend")
    venv_py = os.path.join(TMP, "fake-venv-python")
    open(venv_py, "w").close()
    srv.MLX_VLM_VENV_PY = venv_py
    reset_models()
    srv._model_registry()

    def sb():
        with open(srv.SERVER_BACKEND_FILE) as f:
            return json.load(f)

    check("write ok", srv._write_template_args(POCKET) is True)
    c = sb()
    check("PocketAiHub: backend mlx_vlm, names the model, subfolder 4bit, offline",
          c.get("backend") == "mlx_vlm" and c.get("model") == POCKET
          and c.get("model_subfolder") == "4bit" and c.get("hf_offline") is True, c)
    check("PocketAiHub: no drafter, no trust_remote_code, thinking off",
          "draft_model" not in c and "trust_remote_code" not in c
          and c.get("enable_thinking") is False, c)
    check("no sampling key when the roster sets none", "sampling" not in c, c)

    srv._write_template_args(P27)
    c = sb()
    check("stock 27B: no model_subfolder/sampling, drafter kept, model named",
          "model_subfolder" not in c and "sampling" not in c and c.get("model") == P27
          and c.get("draft_model") == "mlx-community/Qwen3.8-27B-MTP-bf16", c)
    srv._write_template_args(MOE)
    c = sb()
    check("stock MoE: no hf_offline forced, no drafter",
          "hf_offline" not in c and "draft_model" not in c, c)

    rows = read_models()
    for m in rows:
        if m["id"] == HERETIC:
            m["sampling"] = {"repetition_penalty": 1.15, "top_k": "40", "seed": 1}
        if m["id"] == HUIHUI:
            m["model_subfolder"] = "../../etc"
    write_models(rows)
    srv._write_template_args(HERETIC)
    c = sb()
    check("roster sampling is carried VALIDATED (bad keys dropped)",
          c.get("sampling") == {"repetition_penalty": 1.15}, c)
    srv._write_template_args(HUIHUI)
    c = sb()
    check("an invalid model_subfolder is not carried but still forces hf_offline",
          "model_subfolder" not in c and c.get("hf_offline") is True, c)
    # mlx-lm fallback (venv gone) still carries the fields
    os.remove(venv_py)
    srv._write_template_args(POCKET)
    c = sb()
    check("venv missing -> mlx_lm, and the subfolder still rides along",
          c.get("backend") == "mlx_lm" and c.get("model_subfolder") == "4bit"
          and c.get("hf_offline") is True, c)
    reset_models()


def test_downloaded():
    print("\n5. _model_downloaded / switch_model judge <snapshot>/<model_subfolder>")
    reset_models()
    srv._model_registry()
    fake_snapshot(POCKET, model_dir("4bit"))
    check("complete 4bit/ -> downloaded", srv._model_downloaded(POCKET) is True)
    fake_snapshot(POCKET, model_dir("4bit", drop=("model-00002-of-00002.safetensors",)))
    check("4bit/ missing a shard -> not downloaded", srv._model_downloaded(POCKET) is False)
    fake_snapshot(POCKET, model_dir(""))
    check("weights at the ROOT only (wrong variant) -> not downloaded",
          srv._model_downloaded(POCKET) is False)
    r = srv.switch_model(POCKET)
    check("switch_model refuses it before touching anything",
          r == {"ok": False, "error": "model not downloaded yet"}, r)
    check("...and wrote no active-model", not os.path.exists(srv.ACTIVE_MODEL_FILE))
    fake_snapshot(MOE, model_dir(""))
    check("a root model is unchanged (complete root -> downloaded)",
          srv._model_downloaded(MOE) is True)
    fake_snapshot(MOE, model_dir("", drop=("model-00001-of-00002.safetensors",)))
    check("...and a partial root is not", srv._model_downloaded(MOE) is False)
    pay = {m["id"]: m for m in srv.models_payload()["models"]}
    fake_snapshot(POCKET, model_dir("4bit"))
    pay = {m["id"]: m for m in srv.models_payload()["models"]}
    check("models_payload: PocketAiHub downloaded (no drafter needed)",
          pay[POCKET]["downloaded"] is True, pay[POCKET].get("downloaded"))


def test_draft_path():
    print("\n6. _draft_model_path")
    snap = fake_snapshot(ORCA, {"config.json": "{}", "mtp/config.json": "{}"})
    os.makedirs(os.path.join(os.path.dirname(snap), "evil"), exist_ok=True)
    check("draft_subfolder mtp resolves inside the snapshot",
          srv._draft_model_path({"id": ORCA, "draft_subfolder": "mtp"}) == os.path.join(snap, "mtp"))
    check("draft_subfolder ../evil is refused (was joined as-is)",
          srv._draft_model_path({"id": ORCA, "draft_subfolder": "../evil"}) is None)
    check("a separate-repo drafter id passes through",
          srv._draft_model_path({"id": P27, "draft_model": "org/drafter"}) == "org/drafter")


def test_download_jobs():
    print("\n7. download_model scopes the pull")
    calls = []
    lock = threading.Lock()

    def fake_run(argv, repo_id, env=None, stall_s=None, poll_s=None):
        with lock:
            calls.append(json.loads(argv[-1]))
        return 0, "", False

    saved = (srv._dl_run, srv._hf_python, srv._disk_free_gb)
    srv._dl_run = fake_run
    srv._hf_python = lambda: sys.executable
    srv._disk_free_gb = lambda path=None: 4000.0

    def run(mid):
        del calls[:]
        srv._model_dl.pop(mid, None)
        srv._model_dl_err.pop(mid, None)
        r = srv.download_model(mid)
        t0 = time.time()
        while srv._model_dl.get(mid) == "downloading" and time.time() - t0 < 20:
            time.sleep(0.02)
        return r, list(calls), srv._model_dl_err.get(mid)

    try:
        reset_models()
        srv._model_registry()
        shutil.rmtree(os.path.join(HUB, "models--" + POCKET.replace("/", "--")),
                      ignore_errors=True)
        r, jobs, err = run(POCKET)
        check("PocketAiHub: one job, the roster's 4bit/ allow list",
              r.get("ok") and len(jobs) == 1 and jobs[0]["repo_id"] == POCKET
              and jobs[0]["allow_patterns"] == SEEDS[POCKET]["allow_patterns"], (r, jobs))
        check("...with *.py and the pickle formats ignored",
              all(p in jobs[0].get("ignore_patterns", []) for p in ("*.py", "*.bin", "*.pkl")),
              jobs)
        r, jobs, err = run(HERETIC)
        check("Heretic: root allow list, no drafter job",
              len(jobs) == 1 and "*.safetensors" in jobs[0]["allow_patterns"], jobs)
        # (a seed id cannot lose allow_patterns — the backfill restores an
        # absent seed key — so the owner's own multi-variant entry is a custom id)
        rows = read_models()
        rows.append({"id": "me/multi-variant", "label": "mv", "ram": 9,
                     "backend": "mlx_vlm", "model_subfolder": "6bit"})
        rows.append({"id": "me/bad-sub", "label": "bs", "ram": 9,
                     "model_subfolder": "../up"})
        write_models(rows)
        r, jobs, err = run("me/multi-variant")
        check("a subfolder entry WITHOUT allow_patterns pulls only <sub>/*",
              len(jobs) == 1 and jobs[0]["allow_patterns"] == ["6bit/*"]
              and "*.py" in jobs[0]["ignore_patterns"], jobs)
        r, jobs, err = run("me/bad-sub")
        check("an invalid model_subfolder starts no snapshot_download and says why",
              jobs == [] and srv._model_dl.get("me/bad-sub") == "error"
              and "model_subfolder" in (err or ""), (jobs, err))
    finally:
        srv._dl_run, srv._hf_python, srv._disk_free_gb = saved
        reset_models()


def test_sizes():
    print("\n8. _catalog_dl_gb / _model_download_gb")
    check("PocketAiHub quoted from its seed size (16.08 -> 16.1)",
          srv._catalog_dl_gb(POCKET) == 16.1, srv._catalog_dl_gb(POCKET))
    check("orcarouter now has an estimate (root + in-repo mtp/ ~17 GB)",
          srv._catalog_dl_gb(ORCA) == 17.0, srv._catalog_dl_gb(ORCA))
    check("Heretic 24.6, stock MoE 20.4, Huihui 15.2, 9B alt 6.0",
          [srv._catalog_dl_gb(m) for m in (HERETIC, MOE, HUIHUI, BG9)]
          == [24.6, 20.4, 15.2, 6.0], [srv._catalog_dl_gb(m) for m in (HERETIC, MOE, HUIHUI, BG9)])
    check("the 27B still comes from the onboarding catalog (16.08 + 0.87)",
          srv._catalog_dl_gb(P27) == 17.0, srv._catalog_dl_gb(P27))
    check("an unknown id has no estimate", srv._catalog_dl_gb("org/unknown") is None)
    # measured: only the subfolder counts (SPARSE files: st_size without disk)
    lay = model_dir("4bit")
    lay["6bit/model-00001-of-00001.safetensors"] = ""
    snap = fake_snapshot(POCKET, lay)
    gib = 1024 ** 3
    for rel, n in (("4bit/model-00001-of-00002.safetensors", 5 * gib),
                   ("4bit/model-00002-of-00002.safetensors", 3 * gib),
                   ("6bit/model-00001-of-00001.safetensors", 20 * gib)):
        with open(os.path.join(snap, rel), "wb") as f:
            f.truncate(n)
    srv._widget_cache.clear()
    ent = dict(SEEDS[POCKET])
    got = srv._model_download_gb(ent)
    want = srv._dir_size_gb(os.path.join(snap, "4bit"))
    check("downloaded subfolder entry: measured on <snapshot>/4bit only (8 GB), "
          "not the 20 GB 6bit/ beside it",
          got == want == 8.0 and srv._dir_size_gb(snap) == 28.0,
          (got, want, srv._dir_size_gb(snap)))
    srv._widget_cache.clear()


def test_payload():
    print("\n9. models_payload badges")
    reset_models()
    srv._model_registry()
    rows = read_models()
    for m in rows:
        if m["id"] == POCKET:
            m["uncensored"] = False            # a hand edit / an import
            m["verified"] = "yes"
            m["sampling"] = {"repetition_penalty": 1.15, "bogus": 1}
            m["license"] = "  apache-2.0  "
        if m["id"] == MOE:
            m["uncensored"] = True             # the owner may ADD the badge
    rows.append({"id": "me/custom", "label": "c", "ram": 5, "model_subfolder": "../x",
                 "sampling": "fast", "license": 7})
    write_models(rows)
    saved = srv.agent_idle_suspended
    srv.agent_idle_suspended = lambda: True     # no pgrep for the RAM figure
    try:
        pay = srv.models_payload()
    finally:
        srv.agent_idle_suspended = saved
    by = {m["id"]: m for m in pay["models"]}
    keys = ("uncensored", "verified", "model_subfolder", "sampling", "license")
    check("every row carries the five badge keys", all(all(k in m for k in keys)
                                                       for m in pay["models"]))
    p = by[POCKET]
    check("uncensored is sticky to the curated seed (an edit to false does not hide it)",
          p["uncensored"] is True, p["uncensored"])
    check("verified must be a real boolean ('yes' -> None)", p["verified"] is None)
    check("sampling normalized to the valid subset", p["sampling"] == {"repetition_penalty": 1.15})
    check("license trimmed", p["license"] == "apache-2.0", p["license"])
    check("model_subfolder exposed", p["model_subfolder"] == "4bit")
    check("an owner can ADD the uncensored badge", by[MOE]["uncensored"] is True)
    check("stock rows: uncensored False, verified per seed",
          by[P27]["uncensored"] is False and by[P27]["verified"] is True
          and by[MOE]["verified"] is False)
    c = by["me/custom"]
    check("custom row: invalid subfolder/sampling/license -> None",
          c["model_subfolder"] is None and c["sampling"] is None and c["license"] is None
          and c["uncensored"] is False and c["verified"] is None, c)
    check("vanch007: license None, background role rides through",
          by[BG9]["license"] is None and by[BG9].get("role") == "background")
    check("the 1.0.3 fit contract still applies to the new rows (64 GB here or not)",
          all(by[m]["fit"] in ("ok", "tight", "no") for m in NEW))
    check("download_gb quoted for every new row", all(by[m]["download_gb"] for m in NEW),
          {m: by[m]["download_gb"] for m in NEW})
    reset_models()


def test_config():
    print("\n10. aux_config roster import/export")
    clean = srv._cfg_clean_model_entry
    e = clean(dict(SEEDS[POCKET]))
    check("export keeps model_subfolder/uncensored/verified/license/allow_patterns",
          e.get("model_subfolder") == "4bit" and e.get("uncensored") is True
          and e.get("verified") is False and e.get("license") == "apache-2.0"
          and e.get("allow_patterns") == SEEDS[POCKET]["allow_patterns"], e)
    check("export never carries trust_remote_code or the size facts",
          not any(k in e for k in ("trust_remote_code", "size_gb", "draft_size_gb")), e)
    e = clean(dict(SEEDS[BG9]))
    check("license None survives the round trip", "license" in e and e["license"] is None, e)
    e = clean({"id": "org/x", "model_subfolder": "../up", "uncensored": "true",
               "verified": 1, "sampling": {"repetition_penalty": 1.15, "top_k": 999999,
                                           "x": 1}, "license": "<script>"})
    check("import: invalid subfolder, non-boolean flags and a bad licence are DROPPED",
          not any(k in e for k in ("model_subfolder", "uncensored", "verified", "license")), e)
    check("import: sampling keeps only the valid subset", e.get("sampling") == {"repetition_penalty": 1.15}, e)
    e = clean({"id": "org/x", "sampling": {}})
    check("import: an explicit empty sampling ({} = no defaults) is kept", e.get("sampling") == {}, e)
    e = clean({"id": "org/x", "sampling": {"top_k": "40"}})
    check("import: an all-invalid sampling is dropped, not written as {}",
          "sampling" not in e, e)
    live = [dict(SEEDS[POCKET])]
    merged = srv._cfg_merge_roster(live, [clean({"id": POCKET, "uncensored": False})])
    check("merge: an import may write uncensored:false into the row...",
          merged[0].get("uncensored") is False)
    check("...but the payload badge stays on (seed wins)", srv._roster_uncensored(merged[0]) is True)


def test_curation():
    print("\n11. curation")
    cur = srv._curated_model_ids()
    check("every new id is curated", all(m in cur for m in NEW))
    reset_models()
    reg = srv._model_registry()
    write_models([m for m in reg if m["id"] != HERETIC])
    os.chmod(models_file(), 0o600)
    # add_model re-runs _model_registry, whose migration re-inserts the seed
    r = srv.add_model(HERETIC)
    check("re-adding a removed curated id needs no confirm", r == {"ok": True}, r)
    row = next(m for m in read_models() if m["id"] == HERETIC)
    check("...and the row is the full curated shape (backend/template_args)",
          row.get("backend") == "mlx_vlm" and row.get("template_args") == {"enable_thinking": False}
          and "trust_remote_code" not in row, row)
    reset_models()


def main():
    try:
        for t in (test_seeds, test_validators, test_registry, test_backend_file,
                  test_downloaded, test_draft_path, test_download_jobs, test_sizes,
                  test_payload, test_config, test_curation):
            try:
                t()
            except Exception as e:  # a crashed section is a failure, not a skip
                import traceback
                traceback.print_exc()
                check("%s ran" % t.__name__, False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


main()

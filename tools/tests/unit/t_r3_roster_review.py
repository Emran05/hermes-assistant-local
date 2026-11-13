#!/usr/bin/env python3
"""Round 3, cluster a-roster — adversarial REVIEW regressions.

What the implementer's suites did not pin, found by reading the diff against
the design and then running the edge cases:

  1  aux_config import: a model_subfolder / draft_subfolder / license that is
     "valid except for a trailing newline" used to be STORED ("4bit\\n") because
     the validators used re.match with a `$` anchor. fullmatch now.
  2  models_payload `license` badge: re-validated as a licence id (a hand-edited
     models.json value is never passed through as markup).
  3  download_model with a model_subfolder entry whose stored allow_patterns do
     not stay inside the subfolder (the owner repointed PocketAiHub 4bit -> 6bit,
     the seed's `4bit/*` list is still on the row): the pull is re-scoped to
     `<sub>/*` instead of fetching the wrong variant and never completing.
     A list that already lives under the subfolder is left alone.
  4  _catalog_dl_gb: the seed's verified size belongs to the SEED's variant —
     a repointed entry gets no estimate (never a wrong one).
  5  _model_downloaded / switch_model: a row whose model_subfolder is set but
     INVALID is never "downloaded" (the launcher refuses to start it, so a switch
     that wrote active-model and hermes' model.default would take the assistant
     down), even when the repo also has weights at its root.

Loads the REAL server.py against a throwaway HOME with a fake Hugging Face hub
cache; nothing is downloaded and no model is woken.
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


TMP = tempfile.mkdtemp(prefix="hermes-r3-roster-review-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_r3_roster_review",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_r3_roster_review"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP, srv.HOME

P27 = "mlx-community/Qwen3.8-27B-4bit"
ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
POCKET = "PocketAiHub/Qwen3.8-27B-Abliterated-MLX"
HUB = os.path.join(TMP, ".cache", "huggingface", "hub")


def write_models(rows):
    with open(srv.MODELS_FILE, "w") as f:
        json.dump({"models": rows}, f)


def read_models():
    with open(srv.MODELS_FILE) as f:
        return json.load(f)["models"]


def reset_models():
    try:
        os.remove(srv.MODELS_FILE)
    except FileNotFoundError:
        pass


def fake_snapshot(mid, layout, sha="abc123"):
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


def model_dir(prefix):
    pre = (prefix + "/") if prefix else ""
    return {pre + "config.json": "{}",
            pre + "model.safetensors.index.json":
                json.dumps({"weight_map": {"w": "model-00001-of-00001.safetensors"}}),
            pre + "model-00001-of-00001.safetensors": "x"}


def test_config_import():
    print("\n1. aux_config import is strict about the trailing newline")
    clean = srv._cfg_clean_model_entry
    base = {"id": POCKET}
    for k, v in (("model_subfolder", "4bit\n"), ("draft_subfolder", "mtp\n"),
                 ("license", "apache-2.0\n"), ("license", "mit\n")):
        e = clean(dict(base, **{k: v}))
        check("%s=%r is dropped, never stored" % (k, v), k not in e, e)
    for k, v in (("model_subfolder", "4bit"), ("draft_subfolder", "mtp"),
                 ("license", "apache-2.0")):
        e = clean(dict(base, **{k: v}))
        check("%s=%r still imports" % (k, v), e.get(k) == v, e)
    e = clean(dict(base, model_subfolder="a/b/c/d/e"))
    check("a five-segment model_subfolder is dropped", "model_subfolder" not in e, e)


def test_license_badge():
    print("\n2. models_payload license badge")
    b = srv._roster_badges
    check("a licence id passes (trimmed)", b({"id": "x/y", "license": " apache-2.0 "})["license"]
          == "apache-2.0")
    for bad in ("<img src=x onerror=alert(1)>", "Apache License, Version 2.0",
                "mit\n<b>", "", "   ", 7, ["mit"], None, "x" * 41, "-mit"):
        check("license %r is not an id -> None" % (bad,),
              b({"id": "x/y", "license": bad})["license"] is None)
    check("the seeds' real values survive", srv._roster_license("apache-2.0") == "apache-2.0")


def test_download_scope():
    print("\n3. download_model re-scopes a stale allow list to the subfolder")
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
        rows = read_models()
        for m in rows:
            if m["id"] == POCKET:
                m["model_subfolder"] = "6bit"           # owner repoints; the
                #                                         seed's 4bit/* list stays
        write_models(rows)
        shutil.rmtree(os.path.join(HUB, "models--" + POCKET.replace("/", "--")),
                      ignore_errors=True)
        r, jobs, err = run(POCKET)
        check("repointed 6bit with the seed's 4bit/* list -> pulls 6bit/* only",
              r.get("ok") and len(jobs) == 1 and jobs[0]["allow_patterns"] == ["6bit/*"], (r, jobs))
        check("...never-code/never-pickle list still applied",
              all(p in jobs[0].get("ignore_patterns", []) for p in ("*.py", "*.bin", "*.pkl")),
              jobs)
        # the shipped (consistent) row keeps its curated, narrower list
        reset_models()
        srv._model_registry()
        r, jobs, err = run(POCKET)
        check("the shipped row keeps the seed's own allow list",
              len(jobs) == 1 and jobs[0]["allow_patterns"]
              == srv._seed_entry(POCKET)["allow_patterns"], jobs)
        # a list that mixes the subfolder with a root pattern is not "inside"
        rows = read_models()
        for m in rows:
            if m["id"] == POCKET:
                m["allow_patterns"] = ["4bit/*.safetensors", "*.json"]
        write_models(rows)
        r, jobs, err = run(POCKET)
        check("a list with ANY pattern outside the subfolder -> <sub>/* instead",
              jobs and jobs[0]["allow_patterns"] == ["4bit/*"], jobs)
        # ids that merely START with the subfolder name are not "inside" it
        rows = read_models()
        for m in rows:
            if m["id"] == POCKET:
                m["allow_patterns"] = ["4bitevil/*"]
        write_models(rows)
        r, jobs, err = run(POCKET)
        check("'4bitevil/*' is not inside 4bit/ -> re-scoped",
              jobs and jobs[0]["allow_patterns"] == ["4bit/*"], jobs)
        # a non-subfolder entry is untouched by all of this
        reset_models()
        srv._model_registry()
        r, jobs, err = run("mlx-community/Qwen3.6-35B-A3B-4bit")
        check("a plain entry keeps its root allow list",
              jobs and "*.safetensors" in jobs[0]["allow_patterns"], jobs)
    finally:
        srv._dl_run, srv._hf_python, srv._disk_free_gb = saved
        reset_models()


def test_estimate():
    print("\n4. the seed's size estimate belongs to the seed's variant")
    reset_models()
    srv._model_registry()
    check("shipped PocketAiHub row -> 16.1", srv._catalog_dl_gb(POCKET) == 16.1,
          srv._catalog_dl_gb(POCKET))
    rows = read_models()
    for m in rows:
        if m["id"] == POCKET:
            m["model_subfolder"] = "6bit"
    write_models(rows)
    check("repointed to 6bit -> no estimate (fail-open), not the 4bit's 16.1",
          srv._catalog_dl_gb(POCKET) is None, srv._catalog_dl_gb(POCKET))
    rows = read_models()
    for m in rows:
        if m["id"] == POCKET:
            m["model_subfolder"] = "4bit"
    write_models(rows)
    check("pointed back at 4bit -> 16.1 again", srv._catalog_dl_gb(POCKET) == 16.1)
    check("a non-subfolder seed is unaffected",
          srv._catalog_dl_gb("mlx-community/Qwen3.6-35B-A3B-4bit") == 20.4)
    reset_models()


def test_invalid_subfolder_not_downloaded():
    print("\n5. an INVALID model_subfolder is never 'downloaded'")
    reset_models()
    srv._model_registry()
    # orcarouter has weights at the repo ROOT; an invalid subfolder must not
    # make the root read as ready
    fake_snapshot(ORCA, dict(model_dir(""), **{"mtp/config.json": "{}"}))
    check("baseline: root weights present -> downloaded (no subfolder set)",
          srv._model_downloaded(ORCA) is True)
    check("baseline: not flagged invalid", srv._model_subfolder_invalid(ORCA) is False)
    for bad in ("../x", "a\\b", "a/./b", "..", "x" * 70):
        rows = read_models()
        for m in rows:
            if m["id"] == ORCA:
                m["model_subfolder"] = bad
        write_models(rows)
        check("model_subfolder=%r -> flagged invalid" % bad[:12],
              srv._model_subfolder_invalid(ORCA) is True)
        check("...and the root-weights repo is NOT downloaded",
              srv._model_downloaded(ORCA) is False)
    r = srv.switch_model(ORCA)
    check("switch_model refuses it before touching active-model/hermes config",
          r == {"ok": False, "error": "model not downloaded yet"}
          and not os.path.exists(srv.ACTIVE_MODEL_FILE), r)
    rows = read_models()
    for m in rows:
        if m["id"] == ORCA:
            m["model_subfolder"] = ""
    write_models(rows)
    check("an EMPTY model_subfolder means 'none' (not invalid)",
          srv._model_subfolder_invalid(ORCA) is False and srv._model_downloaded(ORCA) is True)
    rows = read_models()
    for m in rows:
        if m["id"] == ORCA:
            m["model_subfolder"] = None
    write_models(rows)
    check("a null model_subfolder means 'none'",
          srv._model_subfolder_invalid(ORCA) is False)
    check("an unknown id is not invalid", srv._model_subfolder_invalid("org/unknown") is False)
    reset_models()


def main():
    test_config_import()
    test_license_badge()
    test_download_scope()
    test_estimate()
    test_invalid_subfolder_not_downloaded()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("FAILED: " + f)
        sys.exit(1)


if __name__ == "__main__":
    main()

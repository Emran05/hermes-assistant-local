// aux_doctor.js copyReport() — a report that could not be fetched must be
// reported, never quietly copied as an error string. No DOM, no dashboard.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = process.env.HERMES_REPO || path.resolve(HERE, "..", "..", "..");
const src = fs.readFileSync(path.join(REPO, "dashboard", "aux_doctor.js"), "utf8");

function harness(fetchImpl) {
  const copied = [];
  const toasts = [];
  const sandbox = {
    console,
    navigator: { clipboard: { writeText: (t) => { copied.push(t); } } },
    fetch: fetchImpl,
    toast: (m) => toasts.push(m),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { api: sandbox.hermesDoctor, copied, toasts };
}

const fails = [];
function check(name, cond, extra) {
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : "  " + JSON.stringify(extra ?? null)));
  if (!cond) fails.push(name);
}

// --- 1. a 500 from /api/doctor -------------------------------------------
{
  const h = harness(async () => ({
    ok: false, status: 500,
    text: async () => "doctor could not run — ImportError: cannot load doctor.py\n",
  }));
  await h.api.copyReport();
  check("500: the HTTP failure is stated", /500/.test(h.api.state.err || ""),
        h.api.state.err);
  check("500: the body of the failure is kept",
        /doctor could not run/.test(h.api.state.err || ""), h.api.state.err);
  check("500: nothing is copied", h.copied.length === 0, h.copied);
  check("500: no success toast", h.toasts.length === 0, h.toasts);
}

// --- 2. a healthy 200 -----------------------------------------------------
{
  const h = harness(async () => ({
    ok: true, status: 200,
    text: async () => "Hermes Assistant · doctor\n\nPASS  Dashboard  reachable\n",
  }));
  await h.api.copyReport();
  check("200: no error", !h.api.state.err, h.api.state.err);
  check("200: the report reaches the clipboard once", h.copied.length === 1, h.copied.length);
  check("200: the copied text is the report",
        /PASS  Dashboard/.test(h.copied[0] || ""), h.copied[0]);
  check("200: the user is told", h.toasts.length === 1 && /copied/i.test(h.toasts[0]),
        h.toasts);
}

// --- 3. fetch throws ------------------------------------------------------
{
  const h = harness(async () => { throw new Error("network down"); });
  await h.api.copyReport();
  check("throw: a thrown fetch is reported, not swallowed", !!h.api.state.err,
        h.api.state.err);
  check("throw: nothing is copied", h.copied.length === 0, h.copied);
  check("throw: no success toast", h.toasts.length === 0, h.toasts);
}

console.log("\nTESTS " + (11 - fails.length) + " passed " +
            fails.length + " failed");
if (fails.length) console.log("FAILED: " + fails.join(", "));
process.exit(fails.length ? 1 : 0);

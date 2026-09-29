#!/usr/bin/env node
/*
 * bench_runner.js — `performance` check command: run a workload N times,
 * collect latencies, and emit a percentile statistic the loop check compares
 * against a threshold (e.g. p95 < 200 ms).
 *
 * No external deps — Node stdlib only, so it runs on any customer box with Node.
 * v1 keeps the statistics simple (percentile over N samples). The hardening
 * backlog (bootstrap-CI, held-out seeds, >=5 mandatory runs) is intentionally
 * out of scope here; this reports the raw percentile honestly.
 *
 * Two workload kinds:
 *   --url <URL>        time an HTTP GET (uses global fetch, Node >= 18)
 *   --command "<cmd>"  time a shell command end-to-end
 *
 * Usage:
 *   node bench_runner.js --url http://localhost:3000/api/health \
 *       --runs 20 --warmup 3 --percentile 95 --out r.json
 *   node bench_runner.js --command "node query.js" --runs 10 --out r.json
 *
 * Output: { "value": <ms>, "percentile": 95, "runs": <n>, "samples_ms": [...] }
 */

const { spawn } = require("node:child_process");
const fs = require("node:fs");

function parseArgs(argv) {
  const a = { runs: 10, warmup: 2, percentile: 95 };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    const v = argv[i + 1];
    if (k === "--url") { a.url = v; i++; }
    else if (k === "--command") { a.command = v; i++; }
    else if (k === "--runs") { a.runs = parseInt(v, 10); i++; }
    else if (k === "--warmup") { a.warmup = parseInt(v, 10); i++; }
    else if (k === "--percentile") { a.percentile = parseFloat(v); i++; }
    else if (k === "--out") { a.out = v; i++; }
  }
  return a;
}

function percentile(sorted, p) {
  if (sorted.length === 0) return null;
  // nearest-rank method
  const rank = Math.ceil((p / 100) * sorted.length);
  const idx = Math.min(sorted.length - 1, Math.max(0, rank - 1));
  return sorted[idx];
}

function nowMs() {
  const [s, ns] = process.hrtime();
  return s * 1000 + ns / 1e6;
}

async function timeOnce(a) {
  const start = nowMs();
  if (a.url) {
    const res = await fetch(a.url);
    await res.arrayBuffer(); // drain body so timing includes transfer
    if (!res.ok) throw new Error(`HTTP ${res.status} from ${a.url}`);
  } else if (a.command) {
    await new Promise((resolve, reject) => {
      const isWin = process.platform === "win32";
      const shell = isWin ? (process.env.COMSPEC || "cmd.exe") : "/bin/sh";
      const flag = isWin ? "/c" : "-c";
      const child = spawn(shell, [flag, a.command], { stdio: "ignore" });
      child.on("error", reject);
      child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`command exited ${code}`))));
    });
  } else {
    throw new Error("provide --url or --command");
  }
  return nowMs() - start;
}

async function main() {
  const a = parseArgs(process.argv);
  if (!a.out) { console.error("bench_runner: --out is required"); process.exit(2); }
  // Reject a degenerate sample count: 0 runs would report 0ms (a false green).
  if (!Number.isInteger(a.runs) || a.runs < 1) {
    fs.writeFileSync(a.out, JSON.stringify({ status: "error", error: `--runs must be >= 1 (got ${a.runs})` }, null, 2) + "\n");
    console.error(`bench_runner: error: --runs must be >= 1 (got ${a.runs})`);
    process.exit(3);
  }

  try {
    for (let i = 0; i < a.warmup; i++) await timeOnce(a); // discard warmups
    const samples = [];
    for (let i = 0; i < a.runs; i++) samples.push(await timeOnce(a));
    const sorted = [...samples].sort((x, y) => x - y);
    const value = Math.round(percentile(sorted, a.percentile) * 100) / 100;
    fs.writeFileSync(a.out, JSON.stringify({
      value,
      percentile: a.percentile,
      runs: a.runs,
      samples_ms: samples.map((s) => Math.round(s * 100) / 100),
    }, null, 2) + "\n");
    console.error(`bench_runner: p${a.percentile}=${value}ms over ${a.runs} runs`);
    process.exit(0);
  } catch (err) {
    // Strict: a failed benchmark writes an error result (no value), so the loop
    // engine's clean-signal rule turns it into an ERROR verdict, never a green.
    fs.writeFileSync(a.out, JSON.stringify({
      status: "error", error: String(err && err.message || err),
    }, null, 2) + "\n");
    console.error(`bench_runner: error: ${err}`);
    process.exit(3);
  }
}

main();

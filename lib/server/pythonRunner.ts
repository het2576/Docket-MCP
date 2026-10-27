import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const cliPath = path.join(root, "backend", "cli.py");
const TIMEOUT_MS = 150_000;

function pythonBin() {
  if (process.env.PYTHON_BIN) return process.env.PYTHON_BIN;
  // Built from parts so the bundler doesn't try to trace (and choke on) the venv symlink.
  const venvPython = [root, ".venv", ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"])].join(path.sep);
  return existsSync(venvPython) ? venvPython : "python3";
}

export class PythonError extends Error {}

export function runPython<T = unknown>(command: string, payload: unknown): Promise<T> {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonBin(), [cliPath, command], { cwd: root, env: process.env });
    let stdout = "";
    let stderr = "";

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new PythonError("The agent took longer than 150 seconds and was stopped. Try a shorter transcript."));
    }, TIMEOUT_MS);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(new PythonError(`Couldn't start Python (${pythonBin()}): ${error.message}. Set PYTHON_BIN in .env.local.`));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      let parsed: unknown;
      try {
        parsed = JSON.parse(stdout);
      } catch {
        parsed = undefined;
      }
      if (code !== 0) {
        const message = (parsed as { error?: string } | undefined)?.error;
        if (stderr) console.error("[docket] python stderr:\n" + stderr);
        reject(new PythonError(message || lastLine(stderr) || `Python exited with code ${code}.`));
        return;
      }
      if (parsed === undefined) {
        reject(new PythonError("The agent returned an unreadable response."));
        return;
      }
      resolve(parsed as T);
    });

    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

function lastLine(text: string) {
  return text.trim().split("\n").filter(Boolean).pop();
}

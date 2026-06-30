import { spawn } from "node:child_process";

const cliPath = "backend/cli.py";

export function runPython(command: string, payload: unknown): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.PYTHON_BIN || "python3", [cliPath, command], {
      env: process.env
    });
    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        const errorMsg = stderr || `Python process exited with code ${code}`;
        console.error("PYTHON CRASHED:", errorMsg);
        reject(new Error(errorMsg));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (error) {
        reject(new Error(`Invalid JSON from Python: ${String(error)}\n${stdout}`));
      }
    });

    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

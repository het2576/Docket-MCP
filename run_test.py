import os
import subprocess
import sys

env = os.environ.copy()
with open(".env.local", "r") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            key, val = line.split("=", 1)
            env[key] = val

result = subprocess.run(["python3", "backend/agent.py", "samples/standup.txt"], env=env)
sys.exit(result.returncode)

import os
import signal
import subprocess


def run():
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    proc = subprocess.Popen(["uv", "run", "pytest", "tests/", "-v", "-s", "-l"], env=env)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("Timeout! Sending SIGABRT to dump threads...")
        os.kill(proc.pid, signal.SIGABRT)


if __name__ == "__main__":
    run()

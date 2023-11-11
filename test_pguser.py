#!/usr/bin/env python
# -*- coding: utf-8 -*-

import subprocess
import time

from kopf.testing import KopfRunner


def test_pguser():
    with KopfRunner(["run", "pguser.py", "--all-namespaces"]) as runner:
        time.sleep(1)
        subprocess.run(["kubectl", "apply", "-f", "sample-pguser.yaml"], check=True)
        time.sleep(1)
        subprocess.run(
            [
                "kubectl",
                "patch",
                "pguser",
                "sampleuser",
                "--type",
                "merge",
                "-p",
                '{"spec":{"password":"newpassword", "username":"newusername", "db":"newdb"}, "metadata": {"labels": {"app.kubernetes.io/name": "newapp", "brandnew": "absolutely"}}}',
            ],
            check=True,
        )
        time.sleep(5)
        subprocess.run(["kubectl", "delete", "-f", "sample-pguser.yaml"], check=True)
        time.sleep(1)

    print(f"exit_code={runner.exit_code}, exception={runner.exception}")
    print(runner.stdout)
    assert runner.exit_code == 0
    assert runner.exception is None
    assert "CREATE ROLE" in runner.stdout
    assert "CREATE DATABASE" in runner.stdout
    assert "ALTER ROLE" in runner.stdout
    assert "ALTER DATABASE" in runner.stdout
    assert "DROP DATABASE" in runner.stdout
    assert "DROP ROLE" in runner.stdout
    assert "does not exist" not in runner.stdout

    # This assertion is only valid if running with --verbose
    # assert "Deleted, really deleted, and we are notified." in runner.stdout


if __name__ == "__main__":
    test_pguser()

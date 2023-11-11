#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import os
import kopf


from kubernetes import config
from kubernetes.client import Configuration
from kubernetes.client.api import core_v1_api
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

# We need to know the namespace to get the postgres pod, and the username the database
# is running under to run psql as that user.
POSTGRES_USERNAME = os.environ.get("POSTGRES_USERNAME", "postgres")
POSTGRES_NAMESPACE = os.environ.get("POSTGRES_NAMESPACE", "postgres")


@kopf.on.probe(id="ok")
def probe_ok(**kwargs):
    return {"status": "ok"}


def hide_secrets(s, secrets=None):
    for secret in secrets or []:
        s = s.replace(secret, "*" * 8)
    return s


def psql(*ddl, logger, secrets=None):
    # Load my config, and create an API client
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    core_v1 = core_v1_api.CoreV1Api()

    for pod in core_v1.list_namespaced_pod(POSTGRES_NAMESPACE).items:
        if (
            "statefulset.kubernetes.io/pod-name" in pod.metadata.labels
            and pod.metadata.labels.get(
                "app.kubernetes.io/name", pod.metadata.labels.get("app")
            )
            == "postgres"
        ):
            podname = pod.metadata.name
            container = pod.spec.containers[0].name
            break
    else:
        raise kopf.PermanentError(
            f"Could not find postgres pod in namespace {POSTGRES_NAMESPACE}"
        )

    resp = stream(
        core_v1.connect_get_namespaced_pod_exec,
        name=podname,
        namespace=POSTGRES_NAMESPACE,
        container=container,
        command=["psql", "-U", POSTGRES_USERNAME],
        stderr=True,
        stdin=True,
        stdout=True,
        tty=False,
        _preload_content=False,
    )

    if not resp.is_open():
        raise kopf.TemporaryError(
            f"Could not open shell to postgres pod {podname} in namespace {POSTGRES_NAMESPACE}"
        )

    statements = iter(ddl)
    while resp.is_open():
        resp.update(timeout=1)

        if resp.peek_stdout():
            logger.info(hide_secrets(resp.read_stdout(), secrets=secrets))
        if resp.peek_stderr():
            logger.error(hide_secrets(resp.read_stderr(), secrets=secrets))

        try:
            stmt = next(statements)
            logger.debug(hide_secrets(stmt, secrets=secrets))
            resp.write_stdin(f"{stmt}\n")
        except StopIteration:
            resp.write_stdin("\\q\n")
            resp.close()


@kopf.on.resume("PostgreSQLUser")
@kopf.on.create("PostgreSQLUser")
async def create(spec, **kwargs):
    logger = kwargs["logger"]
    username = spec["username"]
    # If db is not set, we create a database with the same name as the user
    db = spec.get("db", username)
    password = spec["password"]
    logger.info("Creating PostgreSQLUser '%s/%s'", username, db)

    psql(
        f"CREATE USER {username} WITH ENCRYPTED PASSWORD '{password}';",
        f"CREATE DATABASE {db} OWNER {username};",
        logger=logger,
        secrets=[password],
    )

    return {"username": username, "password": "<created>", "db": db}


@kopf.on.delete("PostgreSQLUser")
async def delete(spec, **kwargs):
    logger = kwargs["logger"]
    username = spec["username"]
    db = spec.get("db", username)
    logger.info("Deleting PostgreSQLUser '%s/%s'", username, db)

    psql(
        # If there are any connected sessions, this will fail: good!
        f"DROP DATABASE IF EXISTS {db};",
        f"DROP USER IF EXISTS {username};",
        logger=logger,
    )


@kopf.on.update("PostgreSQLUser")
async def update(spec, old, new, diff, **kwargs):
    logger = kwargs["logger"]

    ddl = []
    ret = {}
    secrets = []

    # get current username
    username = spec["username"]

    for item in diff:
        op = item[0]
        obj, *tgt = item[1]
        old = item[2]
        new = item[3]

        logger.info(f"op={op}, obj={obj}, tgt={tgt}, old={old}, new={new}")

        if obj == "metadata":
            continue  # we don't care about metadata changes (labels, etc.)

        if obj == "spec" and op == "change":
            match tgt[0]:
                case "username":
                    ddl.append(f"ALTER USER {old} RENAME TO {new};")
                    username = ret["username"] = new
                case "password":
                    ddl.append(
                        f"ALTER USER {username} WITH ENCRYPTED PASSWORD '{new}';"
                    )
                    secrets.extend([old, new])
                    ret["password"] = "<updated>"
                case "db":
                    ddl.append(f"ALTER DATABASE {old} RENAME TO {new};")
                    ret["db"] = new

    logger.info(f"ddl={ddl}")

    psql(*ddl, logger=logger, secrets=secrets)

    return ret

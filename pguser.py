#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import os
from base64 import b64decode

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

# Load the kubernetes configuration from the cluster (or locally for testing)
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()


@kopf.on.probe(id="ok")
def probe_ok(**kwargs):
    return {"status": "ok"}


def hide_secrets(s, secrets=None):
    for secret in secrets or []:
        try:
            s = s.replace(secret, "*" * 8)
        except TypeError:
            continue  # secret is not a string
        except AttributeError:
            break  # s is not a string
    return s


def get_from_secret(namespace, name, key):
    core_v1 = core_v1_api.CoreV1Api()
    try:
        secret = core_v1.read_namespaced_secret(name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    return b64decode(secret.data[key]).decode("utf-8").strip()


def get_from_configmap(namespace, name, key):
    core_v1 = core_v1_api.CoreV1Api()
    try:
        configmap = core_v1.read_namespaced_config_map(name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    return configmap.data[key]


def maybe_string_or_ref(value, namespace):
    # value is either a string, or
    # {valueFrom: {<config|secret>KeyRef: {name: ..., key: ...}}}
    try:
        ref_type = value["valueFrom"]
    except TypeError:
        # It's not a dict, must be a string (or None)
        return value

    try:
        ref = ref_type["secretKeyRef"]
        return get_from_secret(namespace, ref["name"], ref["key"])
    except KeyError:
        ref = ref_type["configMapKeyRef"]
        return get_from_configmap(namespace, ref["name"], ref["key"])
    except (TypeError, KeyError):
        pass


def psql(*ddl, logger, secrets=None):
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

    username = maybe_string_or_ref(spec["username"], kwargs["namespace"])
    # If db is not set, we create a database with the same name as the user
    db = maybe_string_or_ref(spec.get("db"), kwargs["namespace"]) or username
    password = maybe_string_or_ref(spec["password"], kwargs["namespace"])
    superuser = spec.get("superuser", False)

    logger.info("Creating PostgreSQLUser '%s/%s'", username, db)

    ddl = [
        f"CREATE USER {username} WITH ENCRYPTED PASSWORD '{password}';",
        f"CREATE DATABASE {db} OWNER {username};",
    ]
    if superuser:
        ddl.append(f"ALTER USER {username} WITH SUPERUSER;")

    psql(*ddl, logger=logger, secrets=[password])
    return {
        "username": username,
        "password": "<created>",
        "db": db,
        "superuser": superuser,
    }


@kopf.on.update("PostgreSQLUser")
async def update(spec, old, new, diff, **kwargs):
    logger = kwargs["logger"]

    ddl = []
    ret = {}
    secrets = []

    # Get username from old spec, as it may have been updated
    username = maybe_string_or_ref(old["spec"]["username"], kwargs["namespace"])

    for item in diff:
        op = item[0]
        obj, *tgt = item[1]
        prev = maybe_string_or_ref(item[2], kwargs["namespace"])
        nextval = maybe_string_or_ref(item[3], kwargs["namespace"])

        # This will disclose secrets...
        logger.debug(f"op={op}, obj={obj}, tgt={tgt}, prev={prev}, nextval={nextval}")

        if obj == "metadata":
            continue  # We don't care about metadata changes (labels, etc.)

        if obj == "spec" and not op == "change":
            continue  # We don't care (yet) about spec changes that aren't updates

        match tgt[0]:
            case "username":
                ddl.append(f"ALTER USER {prev} RENAME TO {nextval}; -- 9")
                ret["username"] = nextval
            case "password":
                ddl.append(
                    f"ALTER USER {username} WITH ENCRYPTED PASSWORD '{nextval}'; -- 1"
                )
                secrets.extend([prev, nextval])
                ret["password"] = "<updated>"
            case "db":
                ddl.append(f"ALTER DATABASE {prev} RENAME TO {nextval}; -- 2")
                ret["db"] = nextval
            case "superuser":
                ddl.append(
                    f"ALTER USER {username} WITH {'' if nextval else 'NO '}SUPERUSER; -- 3"
                )
                ret["superuser"] = nextval

    # sort by comment number, to ensure we run in the right order. (We can't change the
    # username before we change the password because we need to refer to the username,
    # which may or may not have been changed in the meantime.)
    ddl.sort(key=lambda x: int(x[-1]))
    logger.info(f"ddl={ddl}")

    psql(*ddl, logger=logger, secrets=secrets)

    return ret

"""MQTT on a run's control channel through a throwaway mosquitto container.

The broker account goes to the container as environment variables and the payload on stdin,
so neither appears on any command line."""

import json
import os
import subprocess
import uuid

MQTT_IMAGE = "eclipse-mosquitto:2"
AUTH = '${MQTT_USER:+-u "$MQTT_USER" -P "$MQTT_PASS"}'


class ControlError(RuntimeError):
    """The control channel could not be reached (publish failed, or the probe container timed out)."""


def _probe(network, auth):
    """docker run command and environment of one probe container (named, so it can be removed)."""
    env = dict(os.environ)
    cmd = ["docker", "run", "--rm", "-i", "--name", f"vnapapi-probe-{uuid.uuid4().hex[:12]}",
           "--network", network, "--label", "vnapapi.probe=1"]
    if auth:
        env["MQTT_USER"], env["MQTT_PASS"] = auth
        cmd += ["-e", "MQTT_USER", "-e", "MQTT_PASS"]
    return cmd, env


def _exec(cmd, stdin, env, timeout):
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        # a killed docker CLI leaves its container behind (--rm only acts once it ran)
        subprocess.run(["docker", "rm", "-f", cmd[cmd.index("--name") + 1]], capture_output=True, timeout=30)
        raise ControlError(f"no answer from the control channel within {timeout:g} s (docker busy?)") from None


def _run(network, script, args, auth, stdin=None, timeout=20):
    cmd, env = _probe(network, auth)
    cmd += [MQTT_IMAGE, "sh", "-c", script, "mqtt", *args]
    return _exec(cmd, stdin, env, timeout)


def publish(network, broker, topic, payload, auth, retain=False):
    """Publish one JSON payload (QoS 1); raises ControlError on failure."""
    script = f'exec mosquitto_pub -h "$1" -q 1 -t "$2" -s {"-r " if retain else ""}{AUTH}'
    result = _run(network, script, [broker, topic], auth, stdin=json.dumps(payload))
    if result.returncode != 0:
        raise ControlError(f"publish to {topic} failed: {result.stderr.strip()[:200]}")


def request(network, broker, topic, reply_topic, payload, auth, wait_s=3):
    """Publish an event and wait (up to wait_s) for the station's answer with the same event_id.
    Returns the answer, or None when none came."""
    cmd, env = _probe(network, auth)
    # one container: subscribe first, then publish, so the answer cannot be missed
    script = (f'mosquitto_sub -h "$1" -t "$3" -W "$4" {AUTH} & sleep 0.5; '
              f'mosquitto_pub -h "$1" -q 1 -t "$2" -s {AUTH} || exit 3; wait')
    proc = _exec(cmd + [MQTT_IMAGE, "sh", "-c", script, "mqtt", broker, topic, reply_topic, str(wait_s)],
                 json.dumps(payload), env, wait_s + 20)
    if proc.returncode == 3:
        raise ControlError(f"publish to {topic} failed: {proc.stderr.strip()[:200]}")
    for line in proc.stdout.splitlines():
        try:
            answer = json.loads(line)
        except json.JSONDecodeError:
            continue
        if answer.get("event_id") == payload.get("event_id"):
            return answer
    return None


def read_retained(network, broker, topic_filter, auth, wait_s=2, limit=200):
    """Retained (and immediately following) messages on a topic filter: {topic: payload}."""
    script = f'exec mosquitto_sub -h "$1" -t "$2" -v -W "$3" -C "$4" {AUTH}'
    result = _run(network, script, [broker, topic_filter, str(wait_s), str(limit)], auth, timeout=wait_s + 15)
    out = {}
    for line in result.stdout.splitlines():
        topic, _, payload = line.partition(" ")
        try:
            out[topic] = json.loads(payload)
        except json.JSONDecodeError:
            continue
    return out

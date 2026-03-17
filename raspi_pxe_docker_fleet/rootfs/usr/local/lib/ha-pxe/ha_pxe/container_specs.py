"""Normalize and validate managed container specifications."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from .errors import SpecError
from .text import slug


GIT_HTTP_RE = re.compile(r"^https?://.+\.git(?:#.*)?$")
GIT_SSH_RE = re.compile(r"^git@.+:.+\.git(?:#.*)?$")


def normalize_container_specs(raw: str | None, mqtt_env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    mqtt_defaults = dict(mqtt_env or {})
    items = [_normalize_item(item, mqtt_defaults) for item in _input_entries(raw or "")]
    names = [item["name"] for item in items]
    if len(names) != len(set(names)):
        raise SpecError("container names must be unique; set an explicit name when sources would infer the same one")
    return sort_container_specs(items)


def sort_container_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    lookup = {spec["name"]: spec for spec in specs}
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(name: str) -> None:
        if name in permanent:
            return
        if name in temporary:
            raise SpecError(f"Container dependency cycle detected involving {name}")
        spec = lookup.get(name)
        if spec is None:
            raise SpecError(f"Container depends_on references an unknown container: {name}")

        temporary.add(name)
        for dependency in spec["depends_on"]:
            if dependency == name:
                raise SpecError(f"Container {name} cannot depend on itself")
            if dependency not in lookup:
                raise SpecError(f"Container {name} depends on undefined container {dependency}")
            visit(dependency)
        temporary.remove(name)
        permanent.add(name)
        ordered.append(spec)

    for spec in specs:
        visit(spec["name"])

    return ordered


def specs_to_json(specs: list[dict[str, Any]]) -> str:
    return json.dumps(specs, indent=2, sort_keys=True) + "\n"


def _input_entries(raw: str) -> list[Any]:
    compact = "".join(raw.split())
    if not compact:
        return []
    if compact.startswith("[") or compact.startswith("{"):
        decoded = json.loads(raw)
        if isinstance(decoded, list):
            return decoded
        if isinstance(decoded, dict):
            return [decoded]
        raise SpecError("containers JSON must decode to an object or array")

    entries: list[str] = []
    for line in raw.splitlines():
        line = line.rstrip("\r")
        line = re.sub(r"\s+#.*$", "", line)
        line = line.strip()
        if line:
            entries.append(line)
    return entries


def _normalize_item(item: Any, mqtt_env: dict[str, str]) -> dict[str, Any]:
    if isinstance(item, str):
        raw_item: dict[str, Any] = {"source": item}
    elif isinstance(item, dict):
        raw_item = dict(item)
    else:
        raise SpecError("container entries must be strings or objects")

    source = _normalize_source(raw_item.get("source"), raw_item.get("image"))
    name = slug(str(raw_item.get("name") or _infer_name(source)))
    image = raw_item.get("image")
    if image in (None, ""):
        image = _default_image(source, name)
    image = str(image)

    env = dict(mqtt_env)
    env.update(_normalize_key_values(raw_item.get("env"), "env"))

    normalized = {
        "name": name,
        "image": source["ref"] if source["type"] == "image" else image,
        "source": source,
        "restart": str(raw_item.get("restart", "unless-stopped")),
        "network_mode": str(raw_item.get("network_mode", "")),
        "privileged": bool(raw_item.get("privileged", False)),
        "workdir": str(raw_item.get("workdir", "")),
        "depends_on": _normalize_depends_on(raw_item.get("depends_on")),
        "env": env,
        "labels": _normalize_key_values(raw_item.get("labels"), "labels"),
        "files": _normalize_files(raw_item.get("files")),
        "volumes": _normalize_volumes(raw_item.get("volumes")),
        "ports": _normalize_ports(raw_item.get("ports")),
        "devices": _normalize_string_array(raw_item.get("devices"), "devices"),
        "extra_hosts": _normalize_string_array(raw_item.get("extra_hosts"), "extra_hosts"),
        "command": _normalize_command(raw_item.get("command")),
    }
    return normalized


def _normalize_source(value: Any, image_override: Any) -> dict[str, Any]:
    if value is None and image_override not in (None, ""):
        return {"type": "image", "ref": str(image_override)}
    if isinstance(value, str):
        return _parse_source_string(value)
    if isinstance(value, dict):
        source_type = str(value.get("type", ""))
        if source_type == "image":
            ref = value.get("ref") or value.get("image") or image_override
            if ref in (None, ""):
                raise SpecError("image source requires ref")
            return {"type": "image", "ref": str(ref)}
        if source_type == "git":
            url = value.get("url")
            if url in (None, ""):
                raise SpecError("git source requires url")
            return {
                "type": "git",
                "url": str(url),
                "ref": str(value.get("ref", "main")),
                "context": str(value.get("context", ".")),
                "dockerfile": str(value.get("dockerfile", "Dockerfile")),
                "build_args": _normalize_key_values(value.get("build_args"), "source.build_args"),
            }
        if source_type == "dockerfile_url":
            url = value.get("url")
            if url in (None, ""):
                raise SpecError("dockerfile_url source requires url")
            return {
                "type": "dockerfile_url",
                "url": str(url),
                "dockerfile": str(value.get("dockerfile", "Dockerfile")),
                "build_args": _normalize_key_values(value.get("build_args"), "source.build_args"),
            }
        raise SpecError("unsupported source.type")
    raise SpecError("container source must be a string or object")


def _parse_source_string(raw_source: str) -> dict[str, Any]:
    if GIT_HTTP_RE.match(raw_source) or GIT_SSH_RE.match(raw_source):
        url, _, fragment = raw_source.partition("#")
        parts = fragment.split(":") if fragment else []
        ref = parts[0] if parts and parts[0] else "main"
        context = parts[1] if len(parts) > 1 and parts[1] else "."
        return {
            "type": "git",
            "url": url,
            "ref": ref,
            "context": context,
            "dockerfile": "Dockerfile",
            "build_args": {},
        }
    if raw_source.startswith("http://") or raw_source.startswith("https://"):
        return {
            "type": "dockerfile_url",
            "url": raw_source,
            "dockerfile": "Dockerfile",
            "build_args": {},
        }
    return {"type": "image", "ref": raw_source}


def _infer_name(source: dict[str, Any]) -> str:
    if source["type"] == "image":
        candidate = source["ref"].split("/")[-1].split("@")[0].split(":")[0]
    elif source["type"] == "git":
        candidate = source["url"].split("/")[-1]
        candidate = re.sub(r"\.git$", "", candidate)
    else:
        candidate = source["url"].split("?")[0].split("#")[0].split("/")[-1]
        candidate = re.sub(r"\.[A-Za-z0-9._-]+$", "", candidate)
    return slug(candidate)


def _default_image(source: dict[str, Any], name: str) -> str:
    if source["type"] == "image":
        return str(source["ref"])
    return f"ha-pxe/{name}:managed"


def _normalize_key_values(value: Any, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SpecError(f"{field} must be an object")
    return {str(key): str(val) for key, val in value.items() if val is not None}


def _normalize_string_array(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{field} must be an array")
    if not all(isinstance(item, str) for item in value):
        raise SpecError(f"{field} entries must be strings")
    return [str(item) for item in value]


def _normalize_command(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    raise SpecError("command must be a string or array")


def _normalize_depends_on(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [slug(str(item)) for item in value]
    elif isinstance(value, dict):
        items = [slug(str(item)) for item in value.keys()]
    else:
        raise SpecError("depends_on must be an array or object")
    return list(dict.fromkeys(items))


def _normalize_volumes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError("volumes must be an array")
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized.append(item)
            continue
        if not isinstance(item, dict):
            raise SpecError("volume entries must be strings or objects")
        source = item.get("source", item.get("src"))
        target = item.get("target", item.get("dst", item.get("destination")))
        if source in (None, ""):
            raise SpecError("volume entry requires source")
        if target in (None, ""):
            raise SpecError("volume entry requires target")
        mount = f"{source}:{target}"
        if bool(item.get("read_only", False)):
            mount = f"{mount}:ro"
        normalized.append(mount)
    return normalized


def _normalize_ports(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError("ports must be an array")
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized.append(item)
            continue
        if not isinstance(item, dict):
            raise SpecError("port entries must be strings or objects")
        container_port = item.get("container", item.get("target"))
        host_port = item.get("host", item.get("published"))
        if container_port is None:
            raise SpecError("port entry requires container")
        if host_port is None:
            raise SpecError("port entry requires host")
        protocol = str(item.get("protocol", "tcp"))
        port = f"{host_port}:{container_port}"
        if protocol != "tcp":
            port = f"{port}/{protocol}"
        normalized.append(port)
    return normalized


def _normalize_files(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError("files must be an array")

    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise SpecError("file entries must be objects")
        container_path = item.get("container_path", item.get("path"))
        if not isinstance(container_path, str) or not container_path.startswith("/"):
            raise SpecError("file container_path must be absolute")
        content = item.get("content", "")
        default_format = "text" if isinstance(content, str) else "json"
        normalized.append(
            {
                "container_path": container_path,
                "content": content,
                "format": str(item.get("format", default_format)),
                "mode": str(item.get("mode", "0644")),
                "read_only": bool(item.get("read_only", True)),
            }
        )
    return normalized


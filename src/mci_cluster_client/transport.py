"""HTTP construction and URL validation helpers."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import quote, urlsplit

import httpx

from .exceptions import ValidationError
from .models import TimeoutConfig

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def normalize_nodes(nodes: Iterable[str]) -> tuple[str, ...]:
    """Validate and canonicalize node origins while retaining stable order."""

    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Iterable):
        raise ValidationError("nodes", "expected a collection of node URLs")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_node in nodes:
        node = normalize_node(raw_node)
        if node in seen:
            raise ValidationError("nodes", "duplicate node after canonicalization")
        seen.add(node)
        normalized.append(node)

    if not normalized:
        raise ValidationError("nodes", "at least one node is required")
    return tuple(normalized)


def normalize_node(raw_node: str) -> str:
    """Normalize one bare host or explicit HTTP(S) origin."""

    if not isinstance(raw_node, str):
        raise ValidationError("nodes", "every node must be a string")
    if not raw_node or any(character.isspace() for character in raw_node):
        raise ValidationError("nodes", "node URLs must be non-empty and contain no whitespace")

    candidate = raw_node if "://" in raw_node else f"https://{raw_node}"
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("nodes", "node URL is malformed") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValidationError("nodes", "only http and https schemes are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("nodes", "credentials in node URLs are not allowed")
    if "?" in raw_node:
        raise ValidationError("nodes", "query strings in node URLs are not allowed")
    if "#" in raw_node:
        raise ValidationError("nodes", "fragments in node URLs are not allowed")
    if parsed.path not in {"", "/"}:
        raise ValidationError("nodes", "node URLs must not contain a base path")
    if host is None:
        raise ValidationError("nodes", "node URL must contain a valid host")
    if "[" in parsed.netloc or "]" in parsed.netloc:
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise ValidationError("nodes", "brackets are only valid for IPv6 hosts") from exc

    canonical_host = _canonical_host(host)
    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    canonical_node = f"{scheme}://{canonical_host}{port_suffix}"
    try:
        httpx.URL(canonical_node)
    except httpx.InvalidURL as exc:
        raise ValidationError("nodes", "node URL is not HTTP-compatible") from exc
    return canonical_node


def group_url(node: str, group_id: str) -> str:
    """Build the trailing-slash GET endpoint for one exact group ID."""

    encoded = quote(group_id, safe="")
    return f"{node}/v1/group/{encoded}/"


def collection_url(node: str) -> str:
    """Build the trailing-slash mutation endpoint."""

    return f"{node}/v1/group/"


def httpx_timeout(config: TimeoutConfig) -> httpx.Timeout:
    return httpx.Timeout(
        connect=config.connect,
        read=config.read,
        write=config.write,
        pool=config.pool,
    )


def _canonical_host(host: str) -> str:
    host_without_dot = host.removesuffix(".")
    if not host_without_dot:
        raise ValidationError("nodes", "node URL must contain a valid host")

    try:
        address = ipaddress.ip_address(host_without_dot)
    except ValueError:
        try:
            ascii_host = host_without_dot.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValidationError("nodes", "node host is not valid IDNA") from exc
        if len(ascii_host) > 253 or any(
            not _DNS_LABEL.fullmatch(label) for label in ascii_host.split(".")
        ):
            raise ValidationError("nodes", "node host is not a valid DNS name") from None
        return ascii_host

    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    return address.compressed

"""Public transport serving a town, from OpenStreetMap through the Overpass API."""

import re
import time

import requests

from datagouv import net

NAME = "transport"
COMMUNE_LEVEL_ONLY = False

SOURCE = "OpenStreetMap via the Overpass API"
ATTRIBUTION = "© OpenStreetMap contributors, ODbL"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
SERVER_TIMEOUT_SECONDS = 50
HTTP_TIMEOUT_SECONDS = 60
RETRY_PAUSE_SECONDS = 10
MODES = ("subway", "tram", "bus", "train")
ROUTE_MODES = {
    "subway": "subway",
    "tram": "tram",
    "light_rail": "tram",
    "bus": "bus",
    "trolleybus": "bus",
    "train": "train",
}
REGIONAL_TRAIN_SERVICES = {"regional", "commuter", "suburban"}
REGIONAL_TRAIN_NETWORKS = re.compile(r"\b(TER|RER|Transilien)\b")

QUERY = """[out:json][timeout:{timeout}];
area["ref:INSEE"="{code}"]["boundary"="administrative"]->.a;
.a out ids;
(
  node(area.a)["station"="subway"];
  node(area.a)["railway"="tram_stop"];
  node(area.a)["highway"="bus_stop"];
  node(area.a)["railway"~"^(station|halt)$"];
)->.s;
.s out tags;
node(area.a)["public_transport"~"^(stop_position|platform)$"]->.p;
(
  rel(bn.s)["type"="route"]["route"~"^(subway|tram|light_rail|bus|trolleybus|train)$"];
  rel(bn.p)["type"="route"]["route"~"^(subway|tram|light_rail|bus|trolleybus|train)$"];
);
out tags;"""


class OverpassError(RuntimeError):
    """Every Overpass endpoint failed, or none knows the town's boundary."""


def fetch(code: str) -> dict:
    """Return, per mode, the lines serving INSEE ``code`` and its distinct stops.

    ``modes`` maps ``subway``, ``tram`` (light rail included), ``bus`` (trolleybus
    included) and ``train`` to ``{"lines": [...], "stops": [...]}``: line refs (the
    route name when it has no ref) and stop names, each distinct and sorted. A line
    serves the town when one of its stops lies inside it; train lines are regional
    and commuter ones only, train stops are stations and halts. Unnamed stops are
    not counted. Endpoints are tried in order; raises ``OverpassError`` when all
    fail or when OpenStreetMap has no boundary tagged with ``code``.
    """
    body, endpoint = query(QUERY.format(timeout=SERVER_TIMEOUT_SECONDS, code=code))
    elements = body.get("elements") or []
    if not any(element.get("type") == "area" for element in elements):
        raise OverpassError(f"no OpenStreetMap boundary with ref:INSEE={code}")
    return {
        "source": SOURCE,
        "attribution": ATTRIBUTION,
        "endpoint": endpoint,
        "modes": summarize(elements),
    }


def query(text: str) -> tuple[dict, str]:
    """Run the Overpass query ``text`` on each endpoint in turn; return the body and the endpoint.

    An endpoint fails on any network error, a non-JSON body, or a body whose
    ``remark`` reports a runtime error (a truncated answer). When every endpoint
    fails, those that answered with an HTTP error status (a busy server) are tried
    once more after ``RETRY_PAUSE_SECONDS``. Raises ``OverpassError`` naming every
    failure when no endpoint answers.
    """
    failures = []
    endpoints = ENDPOINTS
    for attempt in range(2):
        busy = []
        for endpoint in endpoints:
            try:
                body = net.post_json(endpoint, {"data": text}, timeout=HTTP_TIMEOUT_SECONDS)
            except requests.HTTPError as error:
                busy.append(endpoint)
                failures.append(f"{endpoint}: HTTPError: {error}")
                continue
            except Exception as error:
                failures.append(f"{endpoint}: {type(error).__name__}: {error}")
                continue
            remark = (body or {}).get("remark") or ""
            if "error" in remark.lower():
                failures.append(f"{endpoint}: {remark}")
                continue
            return body, endpoint
        if attempt == 0 and busy:
            time.sleep(RETRY_PAUSE_SECONDS)
        endpoints = busy
    raise OverpassError("every Overpass endpoint failed - " + "; ".join(failures))


def summarize(elements: list[dict]) -> dict:
    """Group stop nodes and route relations by mode, distinct by name and by line ref."""
    lines = {mode: set() for mode in MODES}
    stops = {mode: set() for mode in MODES}
    for element in elements:
        tags = element.get("tags") or {}
        if element.get("type") == "relation":
            mode = route_mode(tags)
            key = tags.get("ref") or tags.get("name")
            if mode and key:
                lines[mode].add(key)
        elif element.get("type") == "node":
            mode = stop_mode(tags)
            if mode and tags.get("name"):
                stops[mode].add(tags["name"])
    return {
        mode: {"lines": sorted(lines[mode], key=natural_key), "stops": sorted(stops[mode])}
        for mode in MODES
    }


def route_mode(tags: dict):
    """The mode of a route relation, or ``None`` for a long-distance train or another route."""
    mode = ROUTE_MODES.get(tags.get("route"))
    if mode == "train" and not (
        tags.get("service") in REGIONAL_TRAIN_SERVICES
        or REGIONAL_TRAIN_NETWORKS.search(tags.get("network") or "")
    ):
        return None
    return mode


def stop_mode(tags: dict):
    """The mode of a stop node, or ``None`` when it is none of the four."""
    if tags.get("station") == "subway":
        return "subway"
    if tags.get("railway") == "tram_stop" or tags.get("station") in ("tram", "light_rail"):
        return "tram"
    if tags.get("highway") == "bus_stop":
        return "bus"
    if tags.get("railway") in ("station", "halt") and tags.get("station") in (None, "train"):
        return "train"
    return None


def natural_key(text: str) -> list:
    """Sort key putting ``2`` before ``10``: digit runs compare as numbers."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text) if part]

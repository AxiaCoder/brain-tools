"""Public transport serving a town, from OpenStreetMap through the Overpass API."""

import re
import time

import requests

from datagouv import net
from datagouv.towns import normalize

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
LONG_DISTANCE_SERVICES = {"long_distance", "high_speed", "night", "national", "international"}
LONG_DISTANCE_BRANDS = re.compile(
    r"\b(IC|ICE|ICN|TGV|Ouigo|Intercit[ée]s|InterCityExpress|Lyria|Eurostar|Thalys)\b", re.IGNORECASE
)
ROUTES = '["type"="route"]["route"~"^(subway|tram|light_rail|bus|trolleybus|train)$"]'

QUERY = """[out:json][timeout:{timeout}];
area["ref:INSEE"="{code}"]["boundary"="administrative"]->.a;
.a out ids;
(
  nwr(area.a)["station"="subway"];
  nwr(area.a)["railway"="tram_stop"];
  node(area.a)["highway"="bus_stop"];
  nwr(area.a)["railway"~"^(station|halt)$"];
)->.s;
.s out tags;
nwr(area.a)["public_transport"~"^(stop_position|platform)$"]->.p;
(
  rel(bn.s){routes};
  rel(bw.s){routes};
  rel(bn.p){routes};
  rel(bw.p){routes};
)->.r;
.r out tags;
rel(br.r)["type"="route_master"];
out body;"""


class OverpassError(RuntimeError):
    """Every Overpass endpoint failed, or none knows the town's boundary."""


def fetch(code: str) -> dict:
    """Return, per mode, the lines serving INSEE ``code`` and its distinct stops.

    ``modes`` maps ``subway``, ``tram`` (light rail included), ``bus`` (trolleybus
    included) and ``train`` to ``{"lines": [...], "stops": [...]}``: line refs and
    stop names, each distinct and sorted. A line serves the town when one of its
    stops or platforms lies inside it; train lines are regional and commuter ones
    only, train stops are stations and halts. Unnamed stops are not counted; see
    ``summarize`` for what makes lines and stops distinct. Endpoints are tried in
    order; raises ``OverpassError`` when all fail or when OpenStreetMap has no
    boundary tagged with ``code``.
    """
    body, endpoint = query(QUERY.format(timeout=SERVER_TIMEOUT_SECONDS, code=code, routes=ROUTES))
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
    """Group stops and route relations by mode into distinct lines and stop names.

    A route that belongs to a ``route_master`` is the line of that master, so its
    directions and variants count once; see ``line_names``. A multi-valued ref
    ``a;b`` is several lines. Lines and stops are distinct on a
    folded form - leading zeros, case, accents, punctuation and spacing aside - and
    each keeps its alphabetically first spelling.
    """
    masters = {}
    for element in elements:
        if (element.get("tags") or {}).get("type") == "route_master":
            for member in element.get("members") or []:
                masters[member.get("ref")] = element["tags"]
    lines = {mode: {} for mode in MODES}
    stops = {mode: {} for mode in MODES}
    for element in elements:
        tags = element.get("tags") or {}
        if tags.get("type") == "route_master":
            continue
        if element.get("type") == "relation" and tags.get("type") == "route":
            mode = route_mode(tags)
            if mode:
                for name in line_names(tags, masters.get(element.get("id")) or {}):
                    _keep(lines[mode], line_key(name), name)
        else:
            mode = stop_mode(tags)
            if mode and tags.get("name"):
                _keep(stops[mode], normalize(tags["name"]), tags["name"])
    return {
        mode: {
            "lines": sorted(lines[mode].values(), key=natural_key),
            "stops": sorted(stops[mode].values()),
        }
        for mode in MODES
    }


def line_names(route: dict, master: dict) -> list[str]:
    """The lines a route stands for, from its tags and its master's (``{}`` when none).

    Each value of the master's ref, else of the route's ref, else the master's
    name, else the route's name.
    """
    for tags in (master, route):
        refs = [ref.strip() for ref in (tags.get("ref") or "").split(";") if ref.strip()]
        if refs:
            return refs
    name = master.get("name") or route.get("name")
    return [name] if name else []


def line_key(name: str) -> str:
    """Fold a line ref for comparison: ``TER 07``, ``ter 7`` and ``TER7`` are the same line."""
    return re.sub(r"(?<!\d)0+(?=\d)", "", normalize(name).replace(" ", ""))


def _keep(names: dict, key: str, name: str) -> None:
    """Record ``name`` under ``key``, keeping the alphabetically first spelling."""
    if key and (key not in names or name < names[key]):
        names[key] = name


def route_mode(tags: dict):
    """The mode of a route relation, or ``None`` for a long-distance train or another route.

    A train is regional when its network is TER, RER or Transilien, or its service
    is regional, commuter or suburban - unless its service is long-distance or its
    ref, name, network or brand names a long-distance product (TGV, Intercités, ICE...).
    """
    mode = ROUTE_MODES.get(tags.get("route"))
    if mode != "train":
        return mode
    labels = " ".join(tags.get(key) or "" for key in ("ref", "name", "network", "brand"))
    if tags.get("service") in LONG_DISTANCE_SERVICES or LONG_DISTANCE_BRANDS.search(labels):
        return None
    if tags.get("service") in REGIONAL_TRAIN_SERVICES or REGIONAL_TRAIN_NETWORKS.search(
        tags.get("network") or ""
    ):
        return "train"
    return None


def stop_mode(tags: dict):
    """The mode of a stop node or station way, or ``None`` when it is none of the four."""
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

"""The single network entry point of ``datagouv`` - tests replace ``get_json``."""

from typing import Any, Optional

import requests

TIMEOUT_SECONDS = 20
USER_AGENT = "brain-tools-datagouv/0.1"


def get_json(url: str, params: Optional[dict] = None, timeout: float = TIMEOUT_SECONDS) -> Any:
    """GET ``url`` with ``params`` and return the decoded JSON body; ``timeout`` in seconds.

    Raises ``requests.HTTPError`` on a non-2xx status and ``requests.RequestException``
    on any transport failure.
    """
    response = requests.get(
        url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    return response.json()

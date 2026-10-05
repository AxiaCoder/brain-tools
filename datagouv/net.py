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


def download(url: str, path: str, timeout: float = 60) -> None:
    """Stream ``url`` into the file ``path``, overwriting it; ``timeout`` in seconds per read.

    Raises ``requests.HTTPError`` on a non-2xx status and ``requests.RequestException``
    on any transport failure; ``path`` may then hold a partial file.
    """
    with requests.get(
        url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as response:
        response.raise_for_status()
        with open(path, "wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)

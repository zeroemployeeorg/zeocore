"""GitHub API request utilities."""

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import requests

from zeo_core.core.errors import (
    ZeoApiError,
    ZeoAuthenticationError,
    ZeoQuotaExceededError,
)
from zeo_core.core.logging import get_logger

logger = get_logger(__name__)


def _require_response(e: requests.exceptions.HTTPError) -> requests.Response:
    """
    Narrow `HTTPError.response` from `Response | None` to `Response`.

    `requests`' own stubs type `response` as `Response | None` because the
    attribute is genuinely optional at the library level (confirmed via
    `RequestException.__init__`'s own signature). In this codebase the
    invariant holds in practice: the one real call path
    (`response.raise_for_status()`, confirmed via
    `Response.raise_for_status`'s own source) always sets `response=self` on
    the raised error before either `_is_rate_limited_response` or
    `_handle_http_error` ever see it. Rather than assert past the type
    checker with an unchecked `e.response` access (or a blanket
    `# type: ignore`) for an invariant resting partly on an external
    library, fail loudly and specifically if it is ever violated -- the
    same "don't trust, verify" posture as the rest of this module's error
    handling, and shared by both call sites so the guard shape is defined
    once.
    """
    if e.response is None:
        raise ZeoApiError(
            "GitHub API error: HTTPError raised with no response object",
            service="GitHub",
            original_error=e,
        ) from e
    return e.response


def _handle_rate_limit(
    headers: Mapping[str, str],
    url: str,
    attempt: int,
    max_retries: int,
    original_error: Exception | None = None,
) -> None:
    """
    Shared rate-limit disposition for both the pre-emptive header check and
    the HTTPError-caught path: sleep (and let the caller's loop `continue`)
    if retries remain, else raise ZeoQuotaExceededError. Extracted from
    make_request to remove the duplicated wait-time computation and keep
    make_request's own branch count under the C901 threshold; behavior is
    unchanged (same wait formula, same cap at 60s, same error message).

    Raises ZeoQuotaExceededError when retries are exhausted; otherwise
    sleeps and returns normally so the caller can `continue` its loop.
    """
    reset_time = int(headers.get("X-RateLimit-Reset", "0"))
    current_time = int(time.time())
    wait_time = max(1, reset_time - current_time)

    if attempt < max_retries:
        logger.warning(
            f"GitHub API rate limit exceeded. Waiting {wait_time} seconds before retry."
        )
        time.sleep(min(wait_time, 60))  # Wait at most 60 seconds
        return

    kwargs: dict[str, Any] = {}
    if original_error is not None:
        kwargs["original_error"] = original_error
    raise ZeoQuotaExceededError(
        message=(
            f"GitHub API rate limit exceeded. Reset at "
            f"{datetime.fromtimestamp(reset_time)}"
        ),
        service="GitHub",
        resource=url,
        **kwargs,
    ) from original_error


def _is_rate_limited_response(e: requests.exceptions.HTTPError) -> bool:
    """True if an HTTPError's response indicates GitHub rate limiting."""
    response = _require_response(e)
    return response.status_code == 429 or (
        hasattr(response, "headers")
        and "X-RateLimit-Remaining" in response.headers
        and int(response.headers["X-RateLimit-Remaining"]) == 0
    )


def _handle_http_error(
    e: requests.exceptions.HTTPError,
    url: str,
    attempt: int,
    max_retries: int,
    retry_delay: float,
) -> None:
    """
    Disposition for requests.exceptions.HTTPError: auth error, rate limit,
    retryable server error, or a wrapped ZeoApiError. Extracted from
    make_request's except-block for the same C901 reason as
    _handle_rate_limit; behavior/order unchanged.

    Returns normally (meaning: sleep happened, caller should `continue`) or
    raises. Never returns without either raising or having slept.
    """
    response = _require_response(e)
    status_code = response.status_code

    if status_code in (401, 403):
        raise ZeoAuthenticationError(
            f"GitHub API authentication failed: {response.text}",
            service="GitHub",
            original_error=e,
        ) from e

    if _is_rate_limited_response(e):
        _handle_rate_limit(response.headers, url, attempt, max_retries, e)
        return

    if status_code >= 500 and attempt < max_retries:
        wait_time = retry_delay * (2 ** (attempt - 1))
        logger.warning(
            f"GitHub API server error (status {status_code}). "
            f"Retrying in {wait_time:.1f} seconds..."
        )
        time.sleep(wait_time)
        return

    error_message = str(e)
    try:
        error_data = response.json()
        if "message" in error_data:
            error_message = error_data["message"]
    except ValueError, KeyError, AttributeError:
        pass

    raise ZeoApiError(
        f"GitHub API error: {error_message}",
        service="GitHub",
        status_code=status_code,
        api_method=url,
        original_error=e,
    ) from e


def _retry_or_raise_transient_error(
    e: Exception,
    kind: str,
    url: str,
    attempt: int,
    max_retries: int,
    retry_delay: float,
) -> None:
    """
    Shared disposition for ConnectionError/Timeout: sleep with exponential
    backoff if retries remain, else raise a ZeoApiError describing the
    transient failure. Extracted from make_request to remove the duplicated
    retry-or-raise pattern between its ConnectionError and Timeout except
    blocks, keeping make_request's own branch count under the C901
    threshold; behavior/messages are unchanged from the original two blocks.
    """
    if attempt < max_retries:
        wait_time = retry_delay * (2 ** (attempt - 1))
        logger.warning(f"GitHub API {kind}. Retrying in {wait_time:.1f} seconds...")
        time.sleep(wait_time)
        return

    raise ZeoApiError(
        f"GitHub API {kind}: {str(e)}",
        service="GitHub",
        api_method=url,
        original_error=e,
    ) from e


_RETRYABLE_METHODS = frozenset({"GET", "HEAD"})


def make_request(
    session: requests.Session,
    method: str,
    url: str,
    api_url: str,
    timeout: int = 30,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    **kwargs: Any,  # noqa: ANN401 -- passthrough to requests.Session.request; kwargs are genuinely heterogeneous (headers, verify, cert, ...)
) -> requests.Response:
    """Make an HTTP request to the GitHub API; only reads are retried.

    Args:
        session: Requests session with authentication headers
        method: HTTP method (GET, POST, PUT, DELETE)
        url: API endpoint (without base URL)
        api_url: Base API URL
        timeout: Request timeout in seconds
        max_retries: Maximum attempts for a GET or HEAD. Any other method is
            sent exactly once, whatever this says.
        retry_delay: Delay between retries in seconds
        params: URL parameters
        json: JSON body data
        **kwargs: Additional request parameters

    Returns:
        Response object

    Raises:
        ZeoAuthenticationError: If authentication fails
        ZeoQuotaExceededError: If rate limit is exceeded
        ZeoApiError: For other API errors
    """
    if max_retries < 1:
        raise ZeoApiError(
            f"GitHub API error: make_request called with max_retries="
            f"{max_retries} (must be >= 1)",
            service="GitHub",
            api_method=url,
        )

    full_url = f"{api_url}{url}"
    # Only a read is retried. An effect (POST, PUT, PATCH, DELETE) is sent
    # once: after a 5xx, a timeout or a dropped connection its outcome is
    # unknown, and sending it again could open a second PR, post a second
    # comment or merge twice.
    attempts = max_retries if method.upper() in _RETRYABLE_METHODS else 1
    kwargs.setdefault("timeout", timeout)

    for attempt in range(1, attempts + 1):
        try:
            response = session.request(
                method, full_url, params=params, json=json, **kwargs
            )

            # Check for rate limiting - Need to check before raise_for_status
            remaining = int(response.headers.get("X-RateLimit-Remaining", "1"))
            if remaining == 0 or response.status_code == 429:
                _handle_rate_limit(response.headers, url, attempt, attempts)
                continue

            # Check for successful response
            response.raise_for_status()
            return response

        except ZeoQuotaExceededError:
            # If we already raised a ZeoQuotaExceededError, don't catch and
            # re-raise it. This fixes the issue with nested exceptions
            raise

        except requests.exceptions.HTTPError as e:
            _handle_http_error(e, url, attempt, attempts, retry_delay)
            continue

        except requests.exceptions.ConnectionError as e:
            _retry_or_raise_transient_error(
                e, "connection error", url, attempt, attempts, retry_delay
            )
            continue

        except requests.exceptions.Timeout as e:
            _retry_or_raise_transient_error(
                e, "timeout", url, attempt, attempts, retry_delay
            )
            continue

        except Exception as e:
            # For other unexpected errors, don't try to catch our own exceptions
            if isinstance(e, ZeoQuotaExceededError) or isinstance(
                e, ZeoAuthenticationError
            ):
                raise

            # Unexpected errors
            raise ZeoApiError(
                f"Unexpected error in GitHub API request: {str(e)}",
                service="GitHub",
                api_method=url,
                original_error=e,
            ) from e

    # Structurally unreachable: every branch above either returns or raises,
    # and max_retries < 1 is rejected before the loop starts, so the loop
    # always executes at least one iteration. mypy's flow analysis cannot
    # see that (it does not reason about range()'s bounds or about whether
    # a called function always raises), so it flags this function as
    # possibly falling off the end. Close the gap explicitly rather than
    # silence the checker -- this also documents the invariant and would
    # fail loudly if a future edit ever actually broke it.
    raise ZeoApiError(
        "GitHub API error: retry loop exited without returning a response or raising",
        service="GitHub",
        api_method=url,
    )

from typing import Any, Mapping, Optional

class Response:
    status_code: int
    text: str
    def json(self) -> Any: ...

class AsyncSession:
    def __init__(
        self,
        *,
        impersonate: Optional[str] = ...,
        headers: Optional[Mapping[str, str]] = ...,
        timeout: float = ...,
        **kwargs: Any,
    ) -> None: ...
    async def __aenter__(self) -> AsyncSession: ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...
    async def get(self, url: str, **kwargs: Any) -> Response: ...

class Session:
    def __init__(
        self,
        *,
        impersonate: Optional[str] = ...,
        headers: Optional[Mapping[str, str]] = ...,
        timeout: float = ...,
        **kwargs: Any,
    ) -> None: ...
    def __enter__(self) -> Session: ...
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...
    def get(self, url: str, **kwargs: Any) -> Response: ...
    def post(self, url: str, **kwargs: Any) -> Response: ...

def get(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = ...,
    impersonate: Optional[str] = ...,
    timeout: float = ...,
    **kwargs: Any,
) -> Response: ...

def post(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = ...,
    data: Any = ...,
    impersonate: Optional[str] = ...,
    timeout: float = ...,
    **kwargs: Any,
) -> Response: ...

class ServiceError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        code: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.headers = headers or {}

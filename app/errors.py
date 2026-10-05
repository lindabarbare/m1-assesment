"""Kļūdu atbildes pēc līguma (API contract) vienotās kļūdu shēmas."""

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("ezermala.errors")


class SubmissionNotFound(Exception):
    pass


INVALID_STATE_MESSAGE = "Action not allowed in the current status"
FORWARDED_MESSAGE = "Submission has been forwarded to another institution"


class InvalidState(Exception):
    def __init__(self, message: str = INVALID_STATE_MESSAGE):
        super().__init__(message)
        self.message = message


def _field(error: dict) -> str:
    # loc piemērs: ("body", "personalCode"). Pirmais elements ir vieta pieprasījumā.
    if error["type"] == "json_invalid":
        return "request"
    rest = [str(part) for part in error["loc"][1:]]
    return ".".join(rest) if rest else "request"


def _issue(error: dict) -> str:
    if error["type"] == "missing":
        return "REQUIRED"
    if error["type"] == "string_too_long":
        return "TOO_LONG"
    return "INVALID_FORMAT"


def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Ievadīto vērtību atbildē neatkārtojam: tajā var būt personas dati.
        details = [{"field": _field(e), "issue": _issue(e)} for e in exc.errors()]
        return error_response(
            400, "VALIDATION_ERROR", "Request validation failed", details
        )

    @app.exception_handler(SubmissionNotFound)
    async def not_found(request: Request, exc: SubmissionNotFound):
        return error_response(404, "NOT_FOUND", "Submission not found")

    @app.exception_handler(InvalidState)
    async def invalid_state(request: Request, exc: InvalidState):
        return error_response(409, "INVALID_STATE", exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        # Ķermeni nevar nolasīt (piemēram, bojāti baiti): līguma kļūdu shēma.
        if exc.status_code == 400:
            details = [{"field": "request", "issue": "INVALID_FORMAT"}]
            return error_response(
                400, "VALIDATION_ERROR", "Request validation failed", details
            )
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        # Atbildē nav iekšējās informācijas. Žurnālā tikai ceļš un kļūdas tips,
        # jo kļūdas tekstā var būt dati.
        logger.error("Neparedzēta kļūda: %s %s", request.url.path, type(exc).__name__)
        return error_response(500, "INTERNAL_ERROR", "Internal server error")

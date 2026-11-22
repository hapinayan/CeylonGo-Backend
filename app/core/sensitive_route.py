from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class SensitiveInputRoute(APIRoute):
    """Keep raw request values out of validation responses."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content={"detail": [
                    {"loc": error["loc"][:1], "type": error["type"], "msg": "Invalid input"}
                    for error in exc.errors()
                ]})

        return handle

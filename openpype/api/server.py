import asyncio
import importlib
import os
import sys
import fastapi
import pathlib

from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect
from nxtools import log_traceback, logging

from openpype.access.roles import Roles
from openpype.addons import AddonLibrary
from openpype.api.messaging import Messaging
from openpype.api.metadata import app_meta, tags_meta
from openpype.api.responses import ErrorResponse
from openpype.auth.session import Session
from openpype.config import pypeconfig
from openpype.events import dispatch_event
from openpype.exceptions import OpenPypeException, UnauthorizedException
from openpype.graphql import router as graphql_router
from openpype.lib.postgres import Postgres
from openpype.utils import parse_access_token

app = fastapi.FastAPI(
    docs_url=None,
    redoc_url="/docs",
    openapi_tags=tags_meta,
    **app_meta,
)

#
# Static files
#


class AuthStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        request = fastapi.Request(scope, receive)
        access_token = parse_access_token(request.headers.get("Authorization"))
        if access_token:
            try:
                session_data = await Session.check(access_token, None)
            except OpenPypeException:
                pass
            else:
                if session_data:
                    await super().__call__(scope, receive, send)
                    return
        err_msg = "You need to be logged in in order to download this file"
        raise UnauthorizedException(err_msg)


#
# Error handling
#

logging.user = "server"


async def user_name_from_request(request: fastapi.Request) -> str:
    """Get user from request"""

    access_token = parse_access_token(request.headers.get("Authorization"))
    if not access_token:
        return "anonymous"
    try:
        session_data = await Session.check(access_token, None)
    except OpenPypeException:
        return "anonymous"
    if not session_data:
        return "anonymous"
    user_name = session_data.user.name
    assert type(user_name) is str
    return user_name


@app.exception_handler(OpenPypeException)
async def openpype_exception_handler(
    request: fastapi.Request,
    exc: OpenPypeException,
) -> fastapi.responses.JSONResponse:
    user_name = await user_name_from_request(request)

    path = f"[{request.method.upper()}]"
    path += f" {request.url.path.removeprefix('/api')}"

    logging.error(f"{path}: {exc}", user=user_name)

    return fastapi.responses.JSONResponse(
        status_code=exc.status,
        content=ErrorResponse(code=exc.status, detail=exc.detail).dict(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc) -> fastapi.responses.JSONResponse:
    logging.error(f"Validation error\n{exc}")
    detail = "Validation error"  # TODO: Be descriptive, but not too much
    return fastapi.responses.JSONResponse(
        status_code=400,
        content=ErrorResponse(code=400, detail=detail).dict(),
    )


@app.exception_handler(Exception)
async def all_exception_handler(
    request: fastapi.Request,
    exc: Exception,
) -> fastapi.responses.JSONResponse:
    user_name = await user_name_from_request(request)
    path = f"[{request.method.upper()}]"
    path += f" {request.url.path.removeprefix('/api')}"
    logging.error(f"{path}: UNHANDLED EXCEPTION", user=user_name)
    logging.error(exc)
    return fastapi.responses.JSONResponse(
        status_code=500,
        content=ErrorResponse(code=500, detail="Internal server error").dict(),
    )


#
# GraphQL
#

app.include_router(
    graphql_router, prefix="/graphql", tags=["GraphQL"], include_in_schema=False
)


@app.get("/graphiql", include_in_schema=False)
def explorer() -> fastapi.responses.HTMLResponse:
    page = pathlib.Path("static/graphiql.html").read_text()
    page = page.replace("{{ SUBSCRIPTION_ENABLED }}", "false") # TODO
    return fastapi.responses.HTMLResponse(page, 200)


#
# Websocket
#

messaging = Messaging()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    client = await messaging.join(websocket)
    try:
        while True:
            message = await client.receive()
            if message is None:
                continue

            if message["topic"] == "auth":
                await client.authorize(
                    message.get("token"),
                    topics=message.get("subscribe", []),
                )
    except WebSocketDisconnect:
        if client.user_name:
            logging.info(f"{client.user_name} disconnected")
        else:
            logging.info("Anonymous client disconnected")
        del messaging.clients[client.id]


#
# REST endpoints
#


def init_api(target_app: fastapi.FastAPI, plugin_dir: str = "api") -> None:
    """Register API modules to the server"""

    sys.path.insert(0, plugin_dir)
    for module_name in sorted(os.listdir(plugin_dir)):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            log_traceback(f"Unable to initialize {module_name}")
            continue

        if not hasattr(module, "router"):
            logging.error(f"API plug-in '{module_name}' has no router")
            continue

        target_app.include_router(module.router, prefix="/api")

    # Use endpoints function names as operation_ids
    for route in app.routes:
        if isinstance(route, fastapi.routing.APIRoute):
            route.operation_id = route.name


def init_addons(target_app: fastapi.FastAPI) -> None:
    """Serve static files for addon frontends."""
    library = AddonLibrary.getinstance()
    for addon_name, addon_definition in library.items():
        for version in addon_definition.versions:
            addon = addon_definition.versions[version]
            if (fedir := addon.get_frontend_dir()) is not None:
                logging.debug(f"Initializing frontend dir for {addon_name} {version}")
                target_app.mount(
                    f"/addons/{addon_name}/{version}/frontend",
                    StaticFiles(directory=fedir, html=True),
                )
            if (resdir := addon.get_public_dir()) is not None:
                logging.debug(f"Initializing public dir for {addon_name} {version}")
                target_app.mount(
                    f"/addons/{addon_name}/{version}/public",
                    StaticFiles(directory=resdir),
                )
            if (resdir := addon.get_private_dir()) is not None:
                logging.debug(f"Initializing private dir for {addon_name} {version}")
                target_app.mount(
                    f"/addons/{addon_name}/{version}/private",
                    AuthStaticFiles(directory=resdir),
                )


init_api(app, pypeconfig.api_modules_dir)
init_addons(app)


#
# Start up
#


@app.on_event("startup")
async def startup_event() -> None:
    """Startup event.

    This is called after the server is started and:
        - connects to the database
        - loads roles
    """
    retry_interval = 5
    while True:
        try:
            await Postgres.connect()
        except Exception as e:
            msg = " ".join([str(k) for k in e.args])
            logging.error(f"Unable to connect to the database ({msg})")
            logging.info(f"Retrying in {retry_interval} seconds")
            await asyncio.sleep(retry_interval)
        else:
            break

    await Roles.load()
    await messaging.start()
    await dispatch_event("server.started")
    logging.goodnews("Server started")

"""Spawn target that reads provider SSE without importing Django models."""

from http.client import HTTPException

from .gateway import GatewayFailure, stream_reply


def _send_error(connection, category, message):
    try:
        connection.send(("error", (category, message)))
    except (BrokenPipeError, EOFError, OSError):
        pass


def read_gateway_stream(connection, messages, route_id, tools, gateway_url, gateway_api_key):
    try:
        for item in stream_reply(
            messages,
            route_id=route_id,
            tools=tools,
            gateway_url=gateway_url,
            gateway_api_key=gateway_api_key,
        ):
            connection.send(("item", item))
        connection.send(("complete", None))
    except GatewayFailure as error:
        _send_error(connection, "gateway", str(error))
    except OSError as error:
        _send_error(connection, "os", str(error))
    except ValueError as error:
        _send_error(connection, "value", str(error))
    except HTTPException as error:
        _send_error(connection, "http", str(error))
    except Exception:
        _send_error(connection, "gateway", "The free route is currently unavailable.")
    finally:
        connection.close()

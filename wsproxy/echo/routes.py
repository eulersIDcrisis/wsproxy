"""routes.py

Implements the routes for a simple echo (test).
"""
from wsproxy.base.context import JsonWsContext, Route, RouteType


async def echo(endpoint, args):
    await endpoint.next(args)


def get_routes():
    return [
        Route(RouteType.ONCE, "echo", echo)
    ]


class EchoContext(JsonWsContext):

    def __init__(self):
        super(EchoContext, self).__init__(get_routes())

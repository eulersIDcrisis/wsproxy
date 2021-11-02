"""registry.py.

Registry of the (JSON) routes in the system.
"""
import itertools
from wsproxy.protocol.proxy import get_routes as get_proxy_routes
from wsproxy.routes import (
    info, socks5
)


def get_route_mapping():
    """Return a mapping of name -> route."""
    return {
        route.name: route
        for route in itertools.chain(
            get_proxy_routes(), info.get_routes(), socks5.get_routes()
        )
    }

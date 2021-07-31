"""registry.py.

Registry of the (JSON) routes in the system.
"""
import itertools
from wsproxy.routes import (
    info, socks5, tunnel
)


def get_route_mapping():
    """Return a mapping of name -> route."""
    return {
        route.name: route
        for route in itertools.chain(
            info.get_routes(), tunnel.get_routes(), socks5.get_routes()
        )
    }

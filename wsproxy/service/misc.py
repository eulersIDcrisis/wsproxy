"""misc.py.

Miscellany for running certain operations with wsproxy.
"""
from wsproxy.protocol.json import setup_subscription


async def run_socks_proxy(port, state):
    """Run a socks proxy on the given port and connection.

    This proxies all traffic from this socks proxy over the given
    connection (as defined by the passed in WebsocketState).
    """
    pass

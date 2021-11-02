"""misc.py.

Miscellany for running certain operations with wsproxy.
"""
from wsproxy.protocol.json import setup_subscription
from wsproxy.util import get_child_logger


logger = get_child_logger('misc')


async def run_socks_proxy(port, state):
    """Run a socks proxy on the given port and connection.

    This proxies all traffic from this socks proxy over the given
    connection (as defined by the passed in WebsocketState).
    """
    try:
        async with setup_subscription(
            state, 'socks5_proxy', dict(port=port)
        ) as sub:
            async for msg in sub.result_generator():
                logger.info("%s socks5 handler: %s", state.cxn_id, msg)
    except Exception:
        logger.exception("Error starting SOCSKv5 proxy!")


async def run_tunneled_server(port, state):
    """Run a server on the given port and tunnel it to the client.

    This emulates the -L flag for SSH.
    """
    pass

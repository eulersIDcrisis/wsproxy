"""socks5.py.

Implementation of a SOCKS 5 Proxy over the websocket.
"""
import uuid
import socket
import struct
import asyncio
import ipaddress
from contextlib import AsyncExitStack
from tornado import ioloop, tcpserver, tcpclient, iostream, gen
from wsproxy import util
from wsproxy.core import WsContext
from wsproxy.routes import info
from wsproxy.protocol.json import (
    Route, RouteType, setup_subscription, SubscriptionComplete
)
from wsproxy.protocol import proxy


# Create the default logger for SOCKS 5.
logger = util.get_child_logger('socks5')


SOCKS5_VERSION = 0x05

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04


class Socks5Server(util.LocalTcpServer):
    """Base TcpServer that handles SOCKSv5 proxies.

    Subclasses should override to support custom proxying.
    """

    def __init__(self, port):
        """Create a basic SOCKSv5 server on the given port."""
        super(Socks5Server, self).__init__(port)

    async def handle_connection(self, local_stream, protocol, address, port):
        """Handle a single connection opened to the server."""
        client = tcpclient.TCPClient()
        proxy_stream = await client.connect(str(address), port)
        client_host, client_port = proxy_stream.socket.getsockname()

        try:
            await self._complete_socks5_handshake(
                local_stream, client_host, client_port)

            # Await the piped streams.
            await asyncio.gather(
                util.pipe_stream(local_stream, proxy_stream),
                util.pipe_stream(proxy_stream, local_stream))
        except iostream.StreamClosedError:
            logger.debug("Closed stream")

    async def _handle_stream(self, local_stream, addr):
        """Handle reading the given stream with the given bind address."""
        # SOCKS 5 PROXY PROTOCOL
        #
        # (1) Client should send a buffer with:
        # 0x05, <1 byte count>, <"count" bytes>
        #
        # Read the first two bytes.
        res = await local_stream.read_bytes(2)
        version, count = struct.unpack('!BB', res)
        if version != SOCKS5_VERSION:
            logger.warning(
                "Closing stream for address %s: Only SOCKS 5 accepted.",
                addr)
            local_stream.close()
            return
        auth_methods = await local_stream.read_bytes(count)
        # Only 0x00 (No Authentication) is supported for the first pass.
        # Make sure this is included in the client's options.
        if 0x00 not in auth_methods:
            logger.warning(
                "Closing stream for address %s: No supported auth options.",
                addr)
            await local_stream.write(bytes([SOCKS5_VERSION, 0xFF]))
            return
        # Respond with 0x00 as the accepted authentication type.
        await local_stream.write(bytes([SOCKS5_VERSION, 0x00]))

        # Now for the main loop. Read a request.
        res = await local_stream.read_bytes(4)
        version, cmd, _, atyp = struct.unpack('!BBBB', res)
        logger.debug("CMD: %d", cmd)
        # Parse the address
        if atyp == ATYP_IPV4:
            # 4 bytes for IPv4
            buff = await local_stream.read_bytes(4)
            address = ipaddress.IPv4Address(buff)
            logger.debug("Parsed IPv4 address: %s", address)
        elif atyp == ATYP_IPV6:
            # 16 bytes for IPv6 addresses
            buff = await local_stream.read_bytes(16)
            address = ipaddress.IPv6Address(buff)
            logger.debug("Parsed IPv6 address: %s", address)
        elif atyp == ATYP_DOMAIN:
            # Read the next byte to determine the domain name length,
            # then read that.
            count = await local_stream.read_bytes(1)
            count = int.from_bytes(count, 'big', signed=False)
            buff = await local_stream.read_bytes(count)
            address = buff.decode('utf-8')
            logger.debug("Parsed Domain: %s", address)
        else:
            logger.error("Could not parse connection!")
            return

        # Parse out the port.
        port_buff = await local_stream.read_bytes(2)
        port = struct.unpack('!H', port_buff)[0]
        logger.debug("Parsed port: %s", port)

        # Now that the inital portion of the protocol has been parsed, send the
        # request across to the other connection.
        await self.handle_connection(local_stream, 'tcp', str(address), port)

    async def _complete_socks5_handshake(
            self, local_stream, client_host, client_port):
        """Complete the SOCKS5 handshake after reading the destination address.

        Useful and necessary as a part of the protocol to confirm that the
        connection is valid and ready to start processing data.
        """
        # Write this info out to the original stream, now that the connection
        # is established. This is part of the SOCKS5 spec.
        data = struct.pack('!BBB', 0x05, 0x00, 0x00)
        await local_stream.write(data)
        # Write out the address too.
        bind_host = b'localhost'
        try:
            bindaddr = ipaddress.ip_address(bind_host)
            if isinstance(bindaddr, ipaddress.IPv4Address):
                await local_stream.write(bytes(ATYP_IPV4))
                await local_stream.write(bindaddr.packed)
            else:
                await local_stream.write(bytes(ATYP_IPV6))
                await local_stream.write(bindaddr.packed)
        except Exception:
            await local_stream.write([ATYP_DOMAIN, len(bind_host)])
            await local_stream.write(bind_host)
        # Write the port.
        msg = struct.pack('!H', client_port)
        await local_stream.write(msg)


class ProxySocks5Server(Socks5Server):
    """SOCKS5 Proxy server that sends data over the websocket.

    This tunnels the data from the proxy server over a websocket.
    """

    def __init__(self, port, state, connect_callback=None):
        """Create a SOCKS5 server that proxies across a websocket state.

        Parameters
        ----------
        port: int
            The port to run the server on.
        state: WebsocketState
            The websocket state to send the proxied requests to.
        """
        super(ProxySocks5Server, self).__init__(port)
        self.state = state
        self.socket_id = uuid.uuid1()
        self.pending_cxns = {}
        # Only assign the connection callback if it is actually callable.
        self._connect_callback = connect_callback if callable(connect_callback) else None

    async def handle_connection(self, local_stream, protocol, address, port):
        """Open a connection and proxy across this server's WS connection."""
        try:
            if self._connect_callback:
                self._connect_callback(protocol, address, port)
        except Exception as exc:
            logger.warning("Error in connection callback: %s", exc)

        # Run the connection here.
        try:
            async with AsyncExitStack() as exit_stack:
                exit_stack.callback(local_stream.close)

                remote_socket = proxy.ProxySocket(
                    self.state, local_stream, address, port)
                await remote_socket.open()
                exit_stack.push_async_callback(remote_socket.close)

                await self._complete_socks5_handshake(
                    local_stream, remote_socket.bind_host,
                    remote_socket.bind_port)
                await remote_socket.run()
        except (SubscriptionComplete, iostream.StreamClosedError):
            logger.debug("Closing proxy stream")

    def close(self):
        """Close any pending connections and close the server."""
        for fut in self.pending_cxns.values():
            fut.cancel()


async def socks5_proxy_subscription(endpoint, args):
    """Basic subscription to run a local socks5 proxy on the server.

    The websocket endpoint to proxy across can be set by passing in
    the applicable `cxn_id` parameter.
    """
    try:
        port = int(args['port'])
        cxn_id = args.get('cxn_id')

        # Determine which endpoint state to use. In the case of a server,
        # there might be multiple different connection IDs to connect to.
        if cxn_id:
            context = endpoint.state.context
            socks_state = context.connection_mapping.get(cxn_id)
            if not socks_state:
                await endpoint.error("Invalid connection ID.")
                return
        else:
            socks_state = endpoint.state
    except Exception:
        await endpoint.error("Invalid arguments!")
        return
    server = None
    try:
        # Get the WebsocketState.
        server = ProxySocks5Server(port, socks_state)
        server.setup()
        await endpoint.next(dict(port=port))

        await endpoint.state.connection_closed_event.wait()

        # while True:
        #     await asyncio.sleep(10.0)
    except Exception:
        logger.exception('Exception hosting SOCKS5 proxy subscription.')
        await endpoint.error('Error in socks5 proxy!')
    finally:
        if server:
            server.close()
            server.teardown()


def get_routes():
    """Return the routes that pertain to SOCKS5 proxies."""
    return [
        Route(RouteType.SUB, 'socks5_proxy', socks5_proxy_subscription,
              'socks5_proxy'),
    ]


#
# Test app
#
def _run_main():
    import argparse

    parser = argparse.ArgumentParser(description='Test SOCKS5 Proxy.')
    parser.add_argument('--port', dest='port', type=int, default=10000,
                        help="Set the port to run the Proxy server on.")
    parser.add_argument('--proxy_url', dest='proxy_url', type=str, default='')
    args = parser.parse_args()

    util.setup_default_logger()
    # logging.getLogger().setLevel(logging.DEBUG)

    # Get the current IOLoop.
    loop = ioloop.IOLoop.current()

    if args.proxy_url:
        # Setup the proxy URL.
        client = WsClient
        pass
    else:
        server = Socks5Server(None, args.port)
        server.setup()

    loop.start()


if __name__ == '__main__':
    _run_main()

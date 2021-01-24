"""socks5.py.

Implementation of a SOCKS 5 Proxy over the websocket.
"""
import uuid
import socket
import struct
import asyncio
import argparse
import ipaddress
from contextlib import AsyncExitStack
from tornado import ioloop, tcpserver, tcpclient, iostream, gen
from wsproxy import util
from wsproxy.context import WsContext
from wsproxy.routes import info, proxy
from wsproxy.parser.json import Route, RouteType, setup_subscription


# Create the default logger for SOCKS 5.
logger = util.get_child_logger('socks5')


SOCKS5_VERSION = 0x05

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04


class Socks5Server(util.LocalTcpServer):

    def __init__(self, port):
        super(Socks5Server, self).__init__(port)

    async def handle_stream(self, local_stream, address):
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
                "Closing stream for address %s: Only SOCKS 5 accepted.", address)
            local_stream.close()
            return
        auth_methods = await local_stream.read_bytes(count)
        # Only 0x00 (No Authentication) is supported for the first pass.
        # Make sure this is included in the client's options.
        if 0x00 not in auth_methods:
            logger.warning(
                "Closing stream for address %s: No supported auth options.", address)
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
            # Read the next byte to determine the domain name length, the read that.
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

    async def _complete_socks5_handshake(self, local_stream, client_host, client_port):
        # Write this info out to the original stream, now that the connection is
        # established. This is part of the SOCKS5 spec.
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

    # local_stream, 'tcp', str(address), port
    async def handle_connection(self, local_stream, protocol, address, port):
        client = tcpclient.TCPClient()
        proxy_stream = await client.connect(str(address), port)
        client_host, client_port = proxy_stream.socket.getsockname()

        try:
            await self._complete_socks5_handshake(local_stream, client_host, client_port)

            # Await the piped streams.
            await asyncio.gather(
                util.pipe_stream(local_stream, proxy_stream),
                util.pipe_stream(proxy_stream, local_stream))
        except iostream.StreamClosedError:
            logger.debug("Closed stream")


class ProxySocks5Server(Socks5Server):
    """SOCKS5 Proxy server that sends data over the websocket.

    This tunnels the data from the proxy server over a websocket.
    """

    def __init__(self, port, endpoint):
        super(ProxySocks5Server, self).__init__(port)
        self.endpoint = endpoint
        self.socket_id = uuid.uuid1()
        self.pending_cxns = {}

    async def handle_connection(self, local_stream, protocol, address, port):
        async with AsyncExitStack() as exit_stack:
            exit_stack.callback(local_stream.close)

            remote_socket = proxy.ProxySocket(self.endpoint, local_stream, address, port)
            await remote_socket.open()
            exit_stack.push_async_callback(remote_socket.close)

            await self._complete_socks5_handshake(
                local_stream, remote_socket.bind_host, remote_socket.bind_port)
            await remote_socket.run()

        # cxn_id = uuid.uuid1()

        # async with tunnel.RemoteSocket(self.endpoint, address, port) as remote_socket:
        #     # If we get here, the socket is setup, so notify the local socket.
        #     await self._complete_socks5_handshake(
        #         local_stream, remote_socket.bind_host, remote_socket.bind_port)
        #     await remote_socket.proxy_to_stream(local_stream)

        # sub = setup_subscription(
        #     self.endpoint, "proxy_socket", dict(
        #         protocol="tcp", host=str(address), port=port,
        #         socket_id=self.socket_id.hex))
        # async with sub:
        #     # Receive the message implying that we've connected.
        #     msg = await sub.next()
        #     status = msg.get('connection_status')
        #     socket_id = uuid.UUID(msg['socket_id'])
        #     bind_host = msg['bind_host']
        #     bind_port = msg['bind_port']

        #     # Complete the handshake.
        #     # await self._complete_socks5_handshake(local_stream, str(address), port)
        #     await self._complete_socks5_handshake(local_stream, bind_host, bind_port)

        #     # Create the proxied socket with the given socket ID.
        #     # Use this as a context-manager, so everything cleans up
        #     # when this exits or an exception is thrown.
        #     async with tunnel.TcpLocalTunneledSocket(
        #         socket_id, self.endpoint, local_stream
        #     ) as tunneled_socket:
        #         # Start the main loop. Wait for the local socket and for any remote
        #         # messages. The order these events come in could be arbitrary, so
        #         # handle this by listening on both, and awaiting whenever one of the
        #         # futures is ready.
        #         local_future = asyncio.create_task(tunneled_socket.start_reading())
        #         sub_msg_future = asyncio.create_task(sub.next())

        #         while True:
        #             done, pending = await asyncio.wait([
        #                 local_future, sub_msg_future
        #             ], return_when=asyncio.FIRST_COMPLETED)

        #             # Check the subscription.
        #             if sub_msg_future in done:
        #                 msg = sub_msg_future.result()
        #                 status = msg.get('connection_status')
        #                 if status == 'closed' or status == 'error':
        #                     # Close the local socket.
        #                     await tunneled_socket.close()
        #                     return

        #             # Check the local socket.
        #             if local_future in done:
        #                 await sub.close()
        #                 return
        # print("CLOSING CONNECTION to {}:{}".format(address, port))

    def close(self):
        for fut in self.pending_cxns.values():
            fut.cancel()

# class ProxySocks5Server(Socks5Server):
#     """SOCKS5 Proxy server that sends data over the websocket.

#     This tunnels the data from the proxy server over a websocket.
#     """

#     def __init__(self, port, endpoint):
#         super(ProxySocks5Server, self).__init__(port)
#         self.endpoint = endpoint
#         self.socket_id = uuid.uuid1()
#         self.pending_cxns = {}

#     async def handle_connection(self, local_stream, protocol, address, port):
#         cxn_id = uuid.uuid1()

#         async with tunnel.RemoteSocket(self.endpoint, address, port) as remote_socket:
#             # If we get here, the socket is setup, so notify the local socket.
#             await self._complete_socks5_handshake(
#                 local_stream, remote_socket.bind_host, remote_socket.bind_port)
#             await remote_socket.proxy_to_stream(local_stream)

#         # sub = setup_subscription(
#         #     self.endpoint, "proxy_socket", dict(
#         #         protocol="tcp", host=str(address), port=port,
#         #         socket_id=self.socket_id.hex))
#         # async with sub:
#         #     # Receive the message implying that we've connected.
#         #     msg = await sub.next()
#         #     status = msg.get('connection_status')
#         #     socket_id = uuid.UUID(msg['socket_id'])
#         #     bind_host = msg['bind_host']
#         #     bind_port = msg['bind_port']

#         #     # Complete the handshake.
#         #     # await self._complete_socks5_handshake(local_stream, str(address), port)
#         #     await self._complete_socks5_handshake(local_stream, bind_host, bind_port)

#         #     # Create the proxied socket with the given socket ID.
#         #     # Use this as a context-manager, so everything cleans up
#         #     # when this exits or an exception is thrown.
#         #     async with tunnel.TcpLocalTunneledSocket(
#         #         socket_id, self.endpoint, local_stream
#         #     ) as tunneled_socket:
#         #         # Start the main loop. Wait for the local socket and for any remote
#         #         # messages. The order these events come in could be arbitrary, so
#         #         # handle this by listening on both, and awaiting whenever one of the
#         #         # futures is ready.
#         #         local_future = asyncio.create_task(tunneled_socket.start_reading())
#         #         sub_msg_future = asyncio.create_task(sub.next())

#         #         while True:
#         #             done, pending = await asyncio.wait([
#         #                 local_future, sub_msg_future
#         #             ], return_when=asyncio.FIRST_COMPLETED)

#         #             # Check the subscription.
#         #             if sub_msg_future in done:
#         #                 msg = sub_msg_future.result()
#         #                 status = msg.get('connection_status')
#         #                 if status == 'closed' or status == 'error':
#         #                     # Close the local socket.
#         #                     await tunneled_socket.close()
#         #                     return

#         #             # Check the local socket.
#         #             if local_future in done:
#         #                 await sub.close()
#         #                 return
#         # print("CLOSING CONNECTION to {}:{}".format(address, port))

#     def close(self):
#         for fut in self.pending_cxns.values():
#             fut.cancel()


async def socks5_proxy_subscription(endpoint, args):
    try:
        port = int(args['port'])
    except Exception:
        await endpoint.error("Invalid arguments!")
        return
    try:
        server = ProxySocks5Server(port, endpoint)
        server.setup()
        await endpoint.next(dict(port=port))

        while True:
            await asyncio.sleep(10.0)
    finally:
        server.close()
        server.teardown()


def get_routes():
    """Return the routes that pertain to SOCKS5 proxies."""
    return [
        Route(RouteType.SUB, "socks5_proxy", socks5_proxy_subscription)
    ]


#
# Test app
#
def _run_main():
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

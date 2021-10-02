"""tunnel.py.

Routes that handle tunneling connections between endpoints.
"""
import uuid
import weakref
import asyncio
from contextlib import AsyncExitStack, ExitStack
from tornado import iostream, tcpclient
from wsproxy import util
from wsproxy.authentication.context import NotAuthorized
from wsproxy.protocol.json import (
    Route, RouteType, setup_subscription, SubscriptionComplete
)
from wsproxy.protocol.proxy import RawProxyParser


logger = util.get_child_logger('proxy')


DEFAULT_BUFFSIZE = (2 ** 16)


class RawProxyStreamHandler(object):
    """Handler for proxying a socket request.

    This object manages different objects for proxying the contents of one
    stream over to another endpoint using the module:
        wsproxy.protocol.proxy

    Since this tunnels over a websocket (read as TCP) connection, some
    guarantees are implicit (i.e. message integrity, sequence, etc.).
    However, "flow control" is NOT guaranteed by this protocol because the
    sending/receiving endpoints might process the data at different rates,
    causing data to buffer up at one of the endpoints.
    """

    def __init__(self, state, stream, socket_id, buffsize=DEFAULT_BUFFSIZE):
        self.socket_id = socket_id
        self._state = weakref.ref(state)
        self._stream = stream
        self._bytes_read = 0

        self._cond = asyncio.Condition()
        self._buffer = bytearray(buffsize + 18)
        self._buffer[0] = RawProxyParser.opcode
        self._buffer[1:17] = self.socket_id.bytes

    def get_websocket_state(self):
        return self._state()

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, tb):
        await self.close()

    async def open(self):
        state = self.get_websocket_state()
        if state:
            state.add_proxy_socket(self.socket_id, self)

    async def close(self):
        state = self.get_websocket_state()
        if state:
            state.remove_proxy_socket(self.socket_id)
        async with self._cond:
            self._cond.notify_all()

    async def run_read_with_monitor_sub(self, monitor_sub):
        total_count = 0
        recv_count = 0
        cond = asyncio.Condition()

        async def _monitor_update():
            nonlocal recv_count
            async for msg in monitor_sub.result_generator():
                async with cond:
                    recv_count = msg.get('count', recv_count)
                    cond.notify_all()

        monitor_fut = asyncio.create_task(_monitor_update())
        buff = memoryview(self._buffer)
        try:
            while True:
                count = await self._stream.read_into(buff[18:], partial=True)
                state = self.get_websocket_state()
                await state.write_message(buff[:18 + count], binary=True)
                total_count += count
                async with cond:
                    if total_count > recv_count + len(self._buffer):
                        await cond.wait()
        finally:
            if not monitor_fut.done():
                monitor_fut.cancel()

    async def handle_receive(self, msg):
        try:
            async with self._cond:
                await self._stream.write(msg)
                self._bytes_read += len(msg)
                self._cond.notify_all()
        except iostream.StreamClosedError:
            pass

    async def byte_count_update(self):
        async with self._cond:
            await self._cond.wait()
            return self._bytes_read


class ProxySocket(object):
    """Manage sending and receiving data from a proxied socket."""

    def __init__(self, state, local_stream, host, port,
                 protocol='tcp', buffsize=DEFAULT_BUFFSIZE):
        self.state = state
        # Requested destination details.
        self.protocol = protocol
        self.host = host
        self.port = port
        # Remote socket details.
        self.bind_host = None
        self.bind_port = None
        # Socket ID to use.
        self.socket_id = uuid.uuid1()

        # External details.
        self._monitor_sub = None
        self._local_stream = local_stream
        self._proxy_stream = None

        # Local variables
        self._monitor_fut = None
        self._monitor_cond = asyncio.Condition()
        self._recv_count = 0
        self._buff_size = buffsize

        self._close_context = None

    async def __aenter__(self):
        await self.open()

        # Return the reference to self so this can be used as a context-manager.
        return self

    async def __aexit__(self, exc_type, exc_val, tb):
        await self.close()

    async def open(self):
        """Open the remote socket and setup the applicable structures."""
        async with AsyncExitStack() as exit_stack:
            self._proxy_stream = RawProxyStreamHandler(
                self.state, self._local_stream, self.socket_id,
                buffsize=self._buff_size)

            self.state.add_proxy_socket(self.socket_id, self._proxy_stream)
            exit_stack.callback(self.state.remove_proxy_socket, self.socket_id)

            self._monitor_sub = setup_subscription(
                self.state, "proxy_socket", dict(
                    protocol="tcp", host=self.host, port=self.port,
                    socket_id=self.socket_id.hex))
            await self._monitor_sub.open()
            exit_stack.push_async_callback(self._monitor_sub.close)

            # Wait for a response to see if we connected.
            #If not, raise an Exception.
            msg = await self._monitor_sub.next()
            status = msg.get('connection_status')
            if status != 'connected':
                raise Exception("Error connecting.")
            socket_id = uuid.UUID(msg['socket_id'])
            self.bind_host = msg['bind_host']
            self.bind_port = msg['bind_port']

            # Now, schedule the monitor sub to update in the background with
            # the current value of the received bytes.
            self._monitor_fut = asyncio.create_task(self._monitor_update())

            # Everything is setup correctly, so do not run the exit_stack
            # callbacks.
            self._close_context = exit_stack.pop_all()

    async def close(self):
        if self._close_context:
            await self._close_context.aclose()

    async def run(self):
        total_count = 0
        recv_count = 0

        raw_buff = bytearray(18 + self._buff_size)
        raw_buff[0] = RawProxyParser.opcode
        raw_buff[1:17] = self.socket_id.bytes
        buff = memoryview(raw_buff)
        try:
            # while True:
            while self.state.is_connected:
                count = await self._local_stream.read_into(buff[18:], partial=True)
                await self.state.write_message(buff[:18 + count], binary=True)
                total_count += count
                async with self._monitor_cond:
                    if total_count > recv_count + 3 * self._buff_size:
                        await self._monitor_cond.wait()
        except (iostream.StreamClosedError, SubscriptionComplete):
            pass


    async def _monitor_update(self):
        try:
            async for msg in self._monitor_sub.result_generator():
                count = msg.get('count', self._recv_count)
                async with self._monitor_cond:
                    self._recv_count = count
                    self._monitor_cond.notify_all()
        except (SubscriptionComplete, iostream.StreamClosedError):
            return


async def _write_monitor_updates(endpoint, proxy_stream):
    try:
        while endpoint.state.is_connected:
        # while True:
            byte_count = await proxy_stream.byte_count_update()
            await endpoint.next(dict(count=byte_count))
    except (SubscriptionComplete, iostream.StreamClosedError):
        logger.info("Closing proxy socket monitor.")


async def proxy_socket_subscription(endpoint, args):
    # Parse the socket configuration.
    try:
        # Assume TCP by default, as this is the most common.
        protocol = args.get('protocol', 'tcp')
        host = args['host']
        port = args['port']
        try:
            buffsize = int(args.get('buffsize', DEFAULT_BUFFSIZE))
        except Exception:
            raise KeyError('buffsize')
        try:
            socket_id = uuid.UUID(args['socket_id'])
        except Exception:
            raise KeyError('socket_id')
        # For now, just handle TCP
        assert protocol.lower() == 'tcp'

        # Check the proxy request against the current auth manager.
        endpoint.state.auth_manager.check_proxy_request(host, port, protocol)
    except KeyError as ke:
        await endpoint.error(dict(message="Missing or Invalid field: {}".format(ke)))
        return
    except NotAuthorized:
        await endpoint.error(dict(message="Not Authorized."))
        return
    except Exception:
        logger.exception("Error in proxy_socket subscription.")
        await endpoint.error(dict(message="Internal Error"))
        return
    state = endpoint.state

    # Connect to the socket with the parsed information.
    try:
        client = tcpclient.TCPClient()
        local_stream = await client.connect(host, port)
        res = local_stream.socket.getsockname()
        bind_host = res[0]
        bind_port = res[1]
    except iostream.StreamClosedError as exc:
        await endpoint.error(dict(
            connection_status="error", message="Could not connect; stream is not open!"))
        return
    except Exception:
        logger.exception("Error opening connection to %s:%s", host, port)
        await endpoint.error(dict(
            connection_status="error", message="Could not connect."))
        return
    logger.info("Setup proxy (ID: %s) to: %s:%d", socket_id.hex, host, port)

    try:
        async with AsyncExitStack() as exit_stack:
            # Next, check whether the proxy socket monitor is valid. This is needed
            # for throttling; the caller of this sub should appropriately setup the
            # proxy as well, or else this should fail.
            monitor_sub = setup_subscription(
                state, 'proxy_socket_monitor', dict(socket_id=socket_id.hex))
            await monitor_sub.open()
            exit_stack.push_async_callback(monitor_sub.close)

            # Wait for a response from the monitor sub, for some timeout.
            try:
                msg = await asyncio.wait_for(monitor_sub.next(), 5)
                # This should be defined, but just assume 0 in the case of a
                # malformed response.
                count = msg.get('count', 0)
            except asyncio.TimeoutError:
                await endpoint.error(dict(
                    message="Invalid (or non-existent) monitor on your end!"))
                return
            except Exception:
                logger.exception("Unexpected exception in proxy monitor setup!")
                await endpoint.error(dict(message="Unexpected error!"))
                return

            proxy_stream = RawProxyStreamHandler(
                state, local_stream, socket_id, buffsize=buffsize)
            await proxy_stream.open()
            exit_stack.push_async_callback(proxy_stream.close)

            # Now that everything is setup, send the connection over
            # Now that the local proxy is setup, send the connection over.
            await endpoint.next(dict(
                connection_status="connected", socket_id=socket_id.hex,
                bind_host=bind_host, bind_port=bind_port, count=0))

            monitor_fut = asyncio.create_task(
                proxy_stream.run_read_with_monitor_sub(monitor_sub))
            write_fut = asyncio.create_task(_write_monitor_updates(endpoint, proxy_stream))
            exit_stack.callback(monitor_fut.cancel)
            exit_stack.callback(write_fut.cancel)
            await asyncio.gather(monitor_fut, write_fut)

        await endpoint.next(dict(
            connection_status="closed", socket_id=socket_id.hex))
    except (SubscriptionComplete, iostream.StreamClosedError):
        logger.info("Closing proxy socket.")


async def proxy_socket_monitor_subscription(endpoint, args):
    """Subscription that listens on a socket_id and counts the bytes received."""
    try:
        socket_id = uuid.UUID(args['socket_id'])
    except Exception:
        await endpoint.error(dict(message="Invalid arguments!"))
        return
    try:
        handler = endpoint.state.socket_mapping[socket_id]
    except Exception:
        await endpoint.error(dict(message="No matching socket_id found!"))
        return
    # Send the first message in response with the current count (should be 0).
    await endpoint.next(dict(count=0))

    # Now, basically check the proxy socket from scratch every time.
    # When the socket disconnects (for example), the socket will no
    # longer be there, so we can just quietly exit in that case.
    try:
        while True:
            handler = endpoint.state.socket_mapping.get(socket_id)
            if not handler:
                return
            byte_count = await handler.byte_count_update()
            await endpoint.next(dict(count=byte_count))
    except (SubscriptionComplete, iostream.StreamClosedError):
        logger.info("Closing proxy socket monitor.")


async def tunnel_port_subscription(endpoint, args):
    server = None
    try:
        client_id = args.get('client_id')
        host = args['host']
        local_port = args['port']
        remote_port = args['remote_port']
    except Exception:
        endpoint.error("Invalid arguments!")
        return
    # If set, search out the proper endpoint to host the connection.
    if client_id:
        state = endpoint.context.incoming_mapping.get(client_id)
        if not state:
            state = endpoint.context.outgoing_mapping.get(client_id)
    else:
        state = endpoint.state
    if not state:
        endpoint.error("Invalid target.")
        return
    try:
        server = TunnelPortServer(state, local_port, remote_port)
        server.setup()
        event = asyncio.Event()
        await event.wait()
    except Exception:
        logger.exception("Error tunneling connection.")
    finally:
        if server:
            server.close()


class TunnelPortServer(util.LocalTcpServer):

    def __init__(self, state, local_port, remote_port):
        super(TunnelPortServer, self).__init__(local_port)
        self.state = state
        self.remote_port = remote_port

    async def _handle_stream(self, stream, address):
        try:
            async with AsyncExitStack() as exit_stack:
                exit_stack.callback(stream.close)

                remote_socket = ProxySocket(
                    self.state, stream, address, self.remote_port)
                await remote_socket.open()
                exit_stack.push_async_callback(remote_socket.close)

                await remote_socket.run()
        except iostream.StreamClosedError:
            return
        except Exception:
            logger.exception("Invalid error.")


def get_routes():
    return [
        Route(RouteType.SUB, 'proxy_socket', proxy_socket_subscription, 'proxy'),
        Route(RouteType.SUB, 'proxy_socket_monitor', proxy_socket_monitor_subscription, 'proxy'),
        Route(RouteType.SUB, 'tunnel_port', tunnel_port_subscription, 'proxy'),
    ]

"""proxy.py.

Routes that handle the proxy.
"""
import uuid
import weakref
import asyncio
from contextlib import AsyncExitStack, ExitStack
from tornado import iostream, tcpclient
from wsproxy import util
from wsproxy.parser.json import (
    Route, RouteType, setup_subscription, SubscriptionComplete
)
from wsproxy.parser.proxy import RawProxyParser


logger = util.get_child_logger('proxy')


class _RawProxyStream(object):
    """RawProxySocket."""

    def __init__(self, state, stream, socket_id):
        self.socket_id = socket_id
        self._state = weakref.ref(state)
        self._stream = stream
        self._bytes_read = 0
        self._cond = asyncio.Condition()
        self._buffer = bytearray(1024 + 18)
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
        while True:
            count = await self._stream.read_into(buff[18:], partial=True)
            state = self.get_websocket_state()
            await state.write_message(buff[:18 + count], binary=True)
            total_count += count
            async with cond:
                if total_count > recv_count + 1024:
                    await cond.wait()

    async def get_bytes_read(self):
        async with self._cond:
            return self._bytes_read

    async def handle_receive(self, msg):
        async with self._cond:
            await self._stream.write(msg)
            self._bytes_read += len(msg)
            self._cond.notify_all()

    async def byte_count_update(self):
        async with self._cond:
            await self._cond.wait()
            return self._bytes_read


class ProxySocket(object):
    """Manage sending and receiving data from a proxied socket."""

    def __init__(self, state, local_stream, host, port, protocol='tcp', buffsize=1024):
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

        # Local buffers
        # self._raw_write_buff = bytearray(buffsize + 18)
        # view = memoryview(self._raw_write_buff)
        # self._write_buff = view[18:]

        # Local variables
        self._throttle_sub = None
        self._sub = None
        self._received = asyncio.Queue()
        self._buff_size = 1024

    async def __aenter__(self):
        await self.open()

        # Return the reference to self so this can be used as a context-manager.
        return self

    async def __aexit__(self, exc_type, exc_val, tb):
        await self.close()

    async def open(self):
        """Open the remote socket and setup the applicable structures."""
        async with AsyncExitStack() as exit_stack:
            self._proxy_stream = _RawProxyStream(self.state, self._local_stream, self.socket_id)
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

            # Everything is setup correctly, so do not run the exit_stack
            # callbacks.
            exit_stack.pop_all()

    async def close(self):
        if self._monitor_sub:
            await self._monitor_sub.close()
            self._monitor_sub = None
        if self._proxy_stream:
            await self._proxy_stream.close()
            self._proxy_stream = None

        self.state.remove_proxy_socket(self.socket_id)

    async def run(self):
        total_count = 0
        recv_count = 0
        cond = asyncio.Condition()

        async def _monitor_update():
            nonlocal recv_count
            while True:
                print("MONITOR COUNT: {}".format(recv_count))
                count = await self._monitor_sub.next()
                async with cond:
                    recv_count += count
                    cond.notify_all()

        monitor_fut = asyncio.create_task(_monitor_update())
        raw_buff = bytearray(18 + self._buff_size)
        raw_buff[0] = RawProxyParser.opcode
        raw_buff[1:17] = self.socket_id.bytes
        buff = memoryview(raw_buff)
        while True:
            print("READING INTO LOCAL STREAM")
            count = await self._local_stream.read_into(buff[18:], partial=True)
            print("READ INTO LOCAL STREAM: {}".format(count))
            await self.state.write_message(buff[:18 + count], binary=True)
            total_count += count
            async with cond:
                if total_count > recv_count + 1024:
                    await cond.wait()


# async def _remove_proxy_socket_helper(state, socket_id):
#     state.remove_proxy_socket(socket_id)


async def proxy_socket_subscription(endpoint, args):
    # Parse the socket configuration.
    try:
        # Assume TCP by default, as this is the most common.
        protocol = args.get('protocol', 'tcp')
        host = args['host']
        port = args['port']
        try:
            socket_id = uuid.UUID(args['socket_id'])
        except Exception:
            raise KeyError('socket_id')
        # For now, just handle TCP
        assert protocol.lower() == 'tcp'
    except KeyError as ke:
        await endpoint.error(dict(message="Missing or Invalid field: {}".format(ke)))
        return
    except Exception:
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

            proxy_stream = _RawProxyStream(state, local_stream, socket_id)
            await proxy_stream.open()
            exit_stack.push_async_callback(proxy_stream.close)

            # Now that everything is setup, send the connection over
            # Now that the local proxy is setup, send the connection over.
            await endpoint.next(dict(
                connection_status="connected", socket_id=socket_id.hex,
                bind_host=bind_host, bind_port=bind_port, count=0))

            await proxy_stream.run_read_with_monitor_sub(monitor_sub)

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


def get_routes():
    return [
        Route(RouteType.SUB, 'proxy_socket', proxy_socket_subscription),
        Route(RouteType.SUB, 'proxy_socket_monitor', proxy_socket_monitor_subscription),
    ]

"""proxy.py.

Module to implement the parsing logic for "raw" proxy message
types on the websocket connection.
"""
import uuid
import weakref
import asyncio
from contextlib import AsyncExitStack
from tornado import websocket, netutil, iostream, tcpclient
from wsproxy import util
from wsproxy.protocol.json import (
    Route, RouteType, setup_subscription,
    SubscriptionComplete, SubscriptionError
)

logger = util.get_child_logger('raw_proxy')


DEFAULT_BUFFSIZE = (2 ** 16)


class RawProxyParser(object):
    """Basic Proxy Parser for raw message tunneling."""
    # Single-byte character (cast to an int) to identify messages of this type.
    opcode = ord('r')

    async def process_message(self, state, message):
        """Process a message for the given proxy.

        This parser should receive a message of:
        'r', <16 bytes of socket_id>, ....

        It will parse out the socket_id and check if any handlers are regisered
        to receive those bytes. If so, then this will call the handler with all
        of the remaining bytes. This does no other processing and is (as
        advertised) a very 'raw' proxy.
        """
        try:
            # msg[0] is the opcode, which we'll ignore for now.
            # Parse out the UUID of the connection.
            socket_id = uuid.UUID(bytes=message[1:17])
            if state.debug > 1:
                logger.debug("%s RawProxy bytes received: %d", state.cxn_id,
                             len(message))
            if state.debug > 2:
                logger.debug("%s Raw RECV %s: %s", state.cxn_id, socket_id.hex,
                             message[18:])

            handler = state.socket_mapping[socket_id]
            await handler.handle_receive(message[18:])
            # await handler(message[18:])

        except websocket.WebSocketClosedError:
            logger.error("Websocket closed")
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            logger.error("Error parsing proxy command: %s", exc)
        except Exception:
            logger.exception("Unexpected error!")
            error = "Bad Arguments!"


class ProxyBuffer(object):
    """Object managing a bytearray asynchronously."""

    def __init__(self, buffsize, prefix=None):
        self._buffsize = buffsize
        if prefix is not None:
            prefix_length = len(prefix)
            self._raw_buffer = bytearray(buffsize + prefix_length)
            self._buffer = self._raw_buffer[prefix_length:]
        else:
            self._raw_buffer = bytearray(buffsize)
            self._buffer = self._raw_buffer[:]
        self._head = 0
        self._tail = 0
        self._data_count = 0
        # Track the amount of data that has passed through the buffer.
        self._read_count = 0
        self._write_count = 0
        self._cond = asyncio.Condition()
        self._closed = False

    @property
    def raw_contents(self):
        """Return the raw state of the buffer.

        NOTE: This should not generally be called directly because the
        contents of the buffer are better managed by internal methods.
        """
        return self._raw_buffer

    @property
    def contents(self):
        """Return the state of the buffer (ignoring any prefix).

        NOTE: This should not generally be called directly because the
        contents of the buffer are better managed by internal methods.
        """
        return self._buffer

    @property
    def buffsize(self):
        """Return the maximum size/amount of data the buffer can hold."""
        return self._buffsize

    async def close(self):
        """Mark this buffer as closing."""
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    def get_total_bytes_enqueued(self):
        """Return the total number of bytes read into this buffer."""
        return self._read_count

    def get_total_bytes_dequeued(self):
        """Return the total number of bytes written out to this buffer."""
        return self._write_count

    def get_current_count(self):
        """Return the number of bytes currently available in the buffer."""
        size = self._head - self._tail
        if size < 0:
            return size + self._buffsize
        return size

    async def byte_count_update(self):
        """Wait for the buffer to change.

        Returns
        -------
        2-tuple of: total_read, total_written
            Returns the current total number of bytes read and written.
        """
        async with self._cond:
            await self._cond.wait()
            return self._read_count, self._write_count

    async def enqueue(self, data, wait=False):
        """Read/Enqueue data into the buffer.

        NOTE: This is not guaranteed to write all of the data into the buffer,
        but will instead return the number of bytes it was able to add at the
        time.
        """
        async with self._cond:
            count = 0
            while (not self._closed and self._data_count < self._buffsize and
                    count < len(data)):
                count += self._enqueue(data[count:])
            self._cond.notify_all()
            return count

    async def dequeue(self, buff):
        """Write/Dequeue data from the buffer.

        NOTE: This will read as much data as possible into the buffer, but
        no more.
        """
        async with self._cond:
            count = 0
            while self._data_count > 0 and count < len(buff):
                # Explicitly use a memoryview here to make sure we do not
                # assign incorrectly. 'view' is a window of the original
                # buffer.
                view = memoryview(buff)
                count += self._dequeue(view[count:])
            self._cond.notify_all()
            return count

    async def clear(self):
        """Clear the buffer outright and treat it as empty."""
        async with self._cond:
            self._head = 0
            self._tail = 0
            self._data_count = 0
            # Notify that the buffer changed.
            self._cond.notify_all()

    def _enqueue(self, data):
        # Determine how much can be written into the buffer.
        to_queue = min(self._buffsize - self._data_count, len(data))
        if self._head >= self._tail:
            # Write starting from the head up until the end. We can write
            # up to the end of the buffer.
            offset = min(to_queue, self._buffsize - self._head)
        elif self._head < self._tail:
            offset = min(to_queue, self._tail - self._head)
        self._buffer[self._head:self._head + offset] = data[:offset]
        # Update the buffer head and handle wraparound.
        self._head += offset
        if self._head >= self._buffsize:
            self._head -= self._buffsize
        # Update various counters.
        self._data_count += offset
        self._read_count += offset
        return offset

    def _dequeue(self, buff):
        to_remove = min(len(buff), self._data_count)

        if self._head > self._tail:
            offset = min(to_remove, self._head - self._tail)
        else:
            offset = min(to_remove, self._buffsize - self._tail)
        buff[:offset] = self._buffer[self._tail:self._tail + offset]
        # Update the buffer tail and handle wraparound.
        self._tail += offset
        if self._tail >= self._buffsize:
            self._tail -= self._buffsize
        # Update various counters.
        self._data_count -= offset
        self._write_count += offset

        # # Minor optimization: If '_data_count == 0' (no data is buffered),
        # # reset 'head' to 0 to maximize the amount that can be written in
        # # oneshot.
        # if self._data_count == 0:
        #     self._head = 0
        #     self._tail = 0
        return offset


class AuthManagerResolver(netutil.Resolver):

    def initialize(self, resolver, auth_manager):
        self._resolver = resolver
        self._auth_manager = auth_manager

    async def resolve(self, host: str, port: int, family):
        # Check the 'host' and 'port' parameter now.
        self._auth_manager.check_proxy_request(host, port, 'tcp')

        results = await self._resolver.resolve(host, port, family)
        for result in results:
            # Check that each of the outputs is permitted.
            host_port = result[1]
            self._auth_manager.check_proxy_request(host_port[0], host_port[1])
        return results


class RawProxyContext(object):

    def __init__(self, state, socket_id, buffsize=DEFAULT_BUFFSIZE):
        self.socket_id = socket_id
        self._state = weakref.ref(state)
        self._bytes_read = 0
        self._stopped_event = asyncio.Event()

        self._cond = asyncio.Condition()

        # prefix = bytearray(17)
        # prefix[0] = RawProxyParser.opcode
        # prefix[1:17] = socket_id.bytes
        # self._buffer = ProxyBuffer(buffsize, prefix=prefix)

        self._buffer = bytearray(buffsize + 18)
        self._buffer[0] = RawProxyParser.opcode
        self._buffer[1:17] = self.socket_id.bytes
        self._bytes_sent = 0
        self._bytes_acked = 0
        self._send_cond = asyncio.Condition()

    async def _read_from_local(self, buff):
        """Parse any read data from the proxy into the given buffer or stream.

        As the name implies, this is called to read data from across the proxy,
        when the remote source writes data to be received locally.

        Subclasses should override this to actually read in the data to
        the applicable buffer/stream.

        Parameters
        ----------
        buff: bytearray
            The buffer to read data into.

        Returns
        -------
        int:
            Number of bytes that were read into the buffer.
        """
        raise NotImplementedError('Override _read_into_local() in a subclass!')

    async def _write_to_local(self, data):
        """Write out the given data across the proxy.

        As the name implies, this writes data locally to be recieved remotely.

        Subclasses should override this to actually write out the data to
        the applicable buffer/stream.

        Parameters
        ----------
        data: bytes or bytearray
            Data to write out across to the proxy.
        """
        raise NotImplementedError('Override _write_out_local() in a subclass!')

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

    async def monitor_update(self, monitor_sub):
        async for msg in monitor_sub.result_generator():
            async with self._send_cond:
                curr_acked = self._bytes_acked
                self._bytes_acked = msg.get('count', curr_acked)
                self._send_cond.notify_all()

    async def run(self, monitor_sub):
        monitor_fut = asyncio.create_task(self.monitor_update(monitor_sub))
        buff = memoryview(self._buffer)
        try:
            # Since this is tunneling over a Websocket connection, we have most
            # of the TCP guarantees _except_ for Flow Control. To handle this,
            # we basically send some data and wait for the monitor subscription
            # to report how much data was processed so we know when it is okay
            # to continue sending more data. The sizes of these buffers can be
            # configured and tuned.
            while True:
                count = await self._read_from_local(buff[18:])
                state = self.get_websocket_state()
                await state.write_message(buff[:18 + count], binary=True)
                self._bytes_sent += count
                async with self._send_cond:
                    # Wait until we receive a response from the monitor that
                    # these bytes have been processed. This implements Flow
                    # Control for this connection.
                    if self._bytes_sent > self._bytes_acked + len(self._buffer):
                        await self._send_cond.wait()
        finally:
            monitor_fut.cancel()

    async def handle_receive(self, msg):
        try:
            async with self._cond:
                await self._write_to_local(msg)
                self._bytes_read += len(msg)
                self._cond.notify_all()
        except iostream.StreamClosedError:
            pass

    async def byte_count_update(self):
        async with self._cond:
            await self._cond.wait()
            return self._bytes_read, 0


class RawProxyStreamContext(RawProxyContext):
    """Context for proxying a socket across a websocket connection.

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
        self._stream = stream
        super(RawProxyStreamContext, self).__init__(
            state, socket_id, buffsize=buffsize)

    async def _write_to_local(self, msg):
        await self._stream.write(msg)

    async def _read_from_local(self, buff):
        count = await self._stream.read_into(buff, partial=True)
        return count


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
            self._proxy_stream = RawProxyStreamContext(
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
            monitor_fut = asyncio.create_task(self._monitor_update())
            exit_stack.callback(monitor_fut.cancel)

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
                    if total_count > recv_count + self._buff_size:
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
            total_count, _ = await proxy_stream.byte_count_update()
            await endpoint.next(dict(count=total_count))
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
        await endpoint.error("Missing or Invalid field: {}".format(ke))
        return
    except NotAuthorized:
        await endpoint.error("Not Authorized.")
        return
    except Exception:
        logger.exception("Error in proxy_socket subscription.")
        await endpoint.error("Internal Error")
        return
    state = endpoint.state

    # Connect to the socket with the parsed information.
    try:
        resolver = AuthManagerResolver(
            netutil.DefaultExecutorResolver(),
            endpoint.state.auth_manager)
        client = tcpclient.TCPClient(resolver)
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
                await endpoint.error(
                    "Did not receive a response from the monitor!"
                )
                return
            except SubscriptionError as e:
                await endpoint.error("Unexpected error: {}".format(e))
            except Exception:
                logger.exception("Unexpected exception in proxy monitor setup!")
                await endpoint.error("Unexpected error!")
                return

            proxy_stream = RawProxyStreamContext(
                state, local_stream, socket_id, buffsize=buffsize)
            await proxy_stream.open()
            exit_stack.push_async_callback(proxy_stream.close)

            # Now that everything is setup, send the connection over
            # Now that the local proxy is setup, send the connection over.
            await endpoint.next(dict(
                connection_status="connected", socket_id=socket_id.hex,
                bind_host=bind_host, bind_port=bind_port, count=0))

            monitor_fut = asyncio.create_task(
                proxy_stream.run(monitor_sub))
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

    # Now, basically check the proxy socket from scratch every time.
    # When the socket disconnects (for example), the socket will no
    # longer be there, so we can just quietly exit in that case.
    try:
        # Send the first message in response with the current count (should be 0).
        await endpoint.next(dict(count=0))

        while True:
            handler = endpoint.state.socket_mapping.get(socket_id)
            if not handler:
                return
            total_read, _ = await handler.byte_count_update()
            await endpoint.next(dict(count=total_read))
    except (SubscriptionComplete, iostream.StreamClosedError):
        logger.info("Closing proxy socket monitor.")


def get_routes():
    """Return the routes for basic proxy commands."""
    return [
        Route(RouteType.SUB, 'proxy_socket', proxy_socket_subscription, 'proxy'),
        Route(RouteType.SUB, 'proxy_socket_monitor', proxy_socket_monitor_subscription, 'proxy'),
    ]

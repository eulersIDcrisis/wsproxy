"""proxy.py.

Module to implement the parsing logic for "raw" proxy message
types on the websocket connection.
"""
import uuid
import asyncio
from tornado import websocket
from wsproxy import util

logger = util.get_child_logger('raw_proxy')


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
            self._raw_buffer = bytearray(buffsize + len(prefix))
            self._buffer = self._raw_buffer[len(prefix):]
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

    def _enqueue(self, data):
        # Determine how much can be written into the buffer.
        to_queue = min(self._buffsize - self._data_count, len(data))
        print("QUEUE: {}".format(to_queue))
        if self._head >= self._tail:
            # Write starting from the head up until the end. We can write
            # up to the end of the buffer.
            offset = min(to_queue, self._buffsize - self._head)
        elif self._head < self._tail:
            offset = min(to_queue, self._tail - self._head)
        self._buffer[self._head:self._head + offset] = data[:offset]
        self._data_count += offset
        self._head += offset
        # Wrap around.
        if self._head >= self._buffsize:
            self._head -= self._buffsize
        print("CURRENT BUFFER STATE: {} {} {}".format(
            self._head, self._tail, self._data_count))
        return offset

    def _dequeue(self, buff):
        to_remove = min(len(buff), self._data_count)

        if self._head > self._tail:
            offset = min(to_remove, self._head - self._tail)
        else:
            offset = min(to_remove, self._buffsize - self._tail)
        print("OFFSET: {}".format(offset))
        buff[:offset] = self._buffer[self._tail:self._tail + offset]
        self._data_count -= offset
        self._tail += offset
        if self._tail >= self._buffsize:
            self._tail -= self._buffsize
        print("CURRENT BUFFER STATE: {} {} {}".format(
            self._head, self._tail, self._data_count))
        return offset

    async def enqueue(self, data):
        """Read/Enqueue data into the buffer.

        NOTE: This is not guaranteed to write all of the data into the buffer,
        but will instead return the number of bytes it was able to add.
        """
        async with self._cond:
            count = 0
            while self._data_count < self._buffsize and count < len(data):
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
                print("VIEW readonly: {}".format(view.readonly))
                count += self._dequeue(view[count:])
            self._cond.notify_all()
            return count


# class RawProxyContext(object):

#     def __init__(self, state, socket_id, buffsize=DEFAULT_BUFFSIZE):
#         self.socket_id = socket_id
#         self._state = weakref.ref(state)

#         self._read_cond = asyncio.Condition()
#         self._read_count = 0
#         self._read_buffer = bytearray(buffsize + 18)
#         self._read_buffer[0] = RawProxyParser.opcode
#         self._read_buffer[1:17] = self.socket_id.bytes

#         # TODO -- This could probably be optimized to write data more
#         # efficiently (i.e. without a copy), but this works for now.
#         self._write_count = 0
#         self._write_buffer = bytearray(buffsize + 18)
#         self._write_buffer[0] = RawProxyParser.opcode
#         self._write_buffer[1:17] = self.socket_id.bytes

#     def get_websocket_state(self):
#         return self._state()

#     async def __aenter__(self):
#         await self.open()
#         return self

#     async def __aexit__(self, exc_type, exc_val, tb):
#         await self.close()

#     async def open(self):
#         state = self.get_websocket_state()
#         if state:
#             state.add_proxy_socket(self.socket_id, self)

#     async def close(self):
#         state = self.get_websocket_state()
#         if state:
#             state.remove_proxy_socket(self.socket_id)
#         async with self._cond:
#             self._cond.notify_all()

#     async def handle_receive(self, msg):
#         try:
#             async with self._cond:
#                 await self._write_to_local(msg)
#                 self._read_count += len(msg)
#                 self._cond.notify_all()
#         except iostream.StreamClosedError:
#             pass

#     async def write_to_remote(self, data):
#         """Write the given data out to the remote destination.

#         This tunnels the request through the websocket using the internal
#         wsproxy protocol.
#         """
#         index = 0
#         end_index = len(data)
#         buffsize = len(self._write_buffer) - 18
#         while index < end_index:
#             offset = min(end_index - index, buffsize)
#             self._write_buffer[18:] = data[index:index + offset]
#             write_view = self._write_buffer[:18 + offset]
#             await self.state.write_message(write_view, binary=True)
#             index += offset

#     async def bytes_read_update(self):
#         async with self._cond:
#             await self._cond.wait()
#             return self._read_count

#     async def _read_from_local(self, buff):
#         """Parse any read data from the proxy into the given buffer or stream.

#         As the name implies, this is called to read data from across the proxy,
#         when the remote source writes data to be received locally.

#         Subclasses should override this to actually read in the data to
#         the applicable buffer/stream.

#         Parameters
#         ----------
#         buff: bytearray
#             The buffer to read data into.

#         Returns
#         -------
#         int:
#             Number of bytes that were read into the buffer.
#         """
#         raise NotImplementedError('Override _read_into_local() in a subclass!')

#     async def _write_to_local(self, data):
#         """Write out the given data across the proxy.

#         As the name implies, this writes data locally to be received remotely.

#         Subclasses should override this to actually write out the data to
#         the applicable buffer/stream.

#         Parameters
#         ----------
#         data: bytes or bytearray
#             Data to write out across to the proxy.
#         """
#         raise NotImplementedError('Override _write_out_local() in a subclass!')

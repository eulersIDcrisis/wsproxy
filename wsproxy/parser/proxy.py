"""proxy.py.

Module to implement the parsing logic for "raw" proxy message
types on the websocket connection.
"""
import uuid
import asyncio
from tornado import websocket
from wsproxy import util

logger = util.get_child_logger('raw_proxy')


class RawProxySocket(object):
    """Special class that processes socket proxying."""

    def __init__(self, stream, socket_id):
        self._stream = stream
        self._socket_id = socket_id
        self._write_count = 0
        self._read_count = 0

        self._write_queue = asyncio.Queue(maxsize=10)
        self._read_queue = asyncio.Queue(maxsize=10)

        self._buff = bytearray(1024 + 18)
        self._buff[0] = RawProxyParser.opcode
        self._buff[1:17] = socket_id.bytes

    @property
    def bytes_written(self):
        """Return the number of bytes written to the stream.

        NOTE: This returns the number of bytes that written to the passed-in
        stream; the bytes are written from:
            "remote" --> "local" socket.
        """
        return self._write_count

    @property
    def bytes_read(self):
        """Return the number of bytes read to the stream.

        NOTE: This returns the number of bytes that are read from the passed
        stream; the bytes are read from:
            "local" socket --> "remote"
        """
        return self._read_count

    def get_bind_host_port_tuple(self):
        return self._stream.socket.getsockname()

    async def write_chunk(self):
        msg = await self._write_queue.get()
        await self._stream.write(msg)
        print("WRITE: ", len(msg))

    async def read_chunk(self):
        read_view = memoryview(self._buff[18:])
        count = await self._stream.read_into(read_view, partial=True)
        await state.write_message(read_view[0:count + 18])
        bytes_read += count
        print("READ: ", count)

    async def _read_loop(self):
        bytes_read = 0
        while True:
            await self.read_chunk()
            # read_view = memoryview(self._buff[18:])
            # count = await self._stream.read_into(read_view, partial=True)
            # await state.write_message(read_view[0:count + 18])
            # bytes_read += count
            # print("READ: ", count)

    async def _write_loop(self):
        while True:
            await self.write_chunk()

    async def run(self):
        await asyncio.gather(self._read_loop(), self._write_loop())

    async def handle_receive(self, msg):
        await self._write_queue.put(msg)


class RawProxyParser(object):
    """Basic Proxy Parser for raw message tunneling."""
    # A single-byte character (cast to an int) to identify messages of this type.
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
            if state.debug > 0:
                logger.debug("%s RawProxy bytes received: %d", state.cxn_id, len(message))
            if state.debug > 1:
                logger.debug("%s Raw RECV %s: %s", state.cxn_id, socket_id.hex, message[18:])

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


async def write_to_remote_socket(state, socket_id, data):
    """Write the data for the given proxy socket_id."""
    buff = bytearray(17 + len(data))
    buff[0] = RawProxyParser.opcode
    buff[1:17] = socket_id.bytes
    buff[18:] = data
    await state.write_message(buff, binary=True)

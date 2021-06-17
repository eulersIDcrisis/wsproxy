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

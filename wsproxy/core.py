"""core.py.

Core infrastructure for server/client management.

This module outlines these key types:
 - WsContext
 - WebsocketState
"""
import json
import uuid
import weakref
import asyncio
from functools import wraps
from enum import Enum
from functools import partial
from tornado import tcpserver, tcpclient, websocket, ioloop, httpclient
from wsproxy.util import main_logger as logger
from wsproxy.parser.json import JsonParser
from wsproxy.parser.proxy import RawProxyParser


#
# Connection Objects
#
def generate_connection_id():
    return uuid.uuid1().hex[4:8]


class WsClientConnection(object):
    """Handle an outgoing connection to some server."""

    def __init__(self, context, url_or_request):
        self.cxn_id = generate_connection_id()

        if isinstance(url_or_request, str):
            self.url = url_or_request
            self.request = httpclient.HTTPRequest(
                self.url, connect_timeout=10)
        else:
            self.request = url_or_request
            self.url = self.request.url
        # For now, override the request timeout to 10 seconds (?)
        self.request.connect_timeout = 10

        self.context = context
        self._is_connected = asyncio.Event()

    @property
    def is_connected(self):
        return self._is_connected

    @property
    def cxn(self):
        return self._cxn

    @property
    def state(self):
        return self._state

    async def open(self):

        self._cxn = await websocket.websocket_connect(
            self.request, compression_options={},
            ping_interval=5, ping_timeout=15)
        state = await self.context.add_outgoing_connection(
            self.cxn_id, self, other_url=self.url)
        asyncio.create_task(self._run())
        self._is_connected.set()
        self._state = state
        return state

    async def _run(self):
        # After connecting, listen for messages until the connection closes.
        try:
            while True:
                msg = await self.cxn.read_message()
                if msg is None:
                    return
                asyncio.create_task(self.state.on_message(msg))
        finally:
            await self.context.remove_outgoing_connection(self.cxn_id)
            self._is_connected.clear()

    async def write_message(self, msg, binary=False):
        await self._cxn.write_message(msg, binary=binary)


class WsServerHandler(websocket.WebSocketHandler):
    """Handle connection requests for a server.

    This maps all of the reads and writes from the tornado Websocket to the
    passed 'processor' aggregate class.
    """

    def initialize(self, context=None):
        self._context = weakref.ref(context)
        self._state = None
        self.client_ip = None
        self.client_port = None
        self.cxn_id = None

    @property
    def context(self):
        return self._context()

    @property
    def state(self):
        return self._state

    @property
    def url(self):
        return u'{}:{}'.format(self.client_ip, self.client_port)

    async def open(self):        
        self.cxn_id = generate_connection_id()
        if self.request.connection.stream is None:
            args = self.request.connection.context.address
            url = "{}:{}".format(*args)
        else:
            url = "{}:{}".format(
                self.request.remote_ip,
                self.request.connection.stream.socket.getpeername()[1]
            )
        self._state = await self.context.add_incoming_connection(self.cxn_id, self, other_url=url)
        logger.info("Client CXN (ID: %s) received from: %s", self.cxn_id, self._state.other_url)
        logger.info("Received at URL: %s", self.request.full_url())
        

    def on_close(self):
        try:
            logger.info("Closing client CXN with ID: %s", self.cxn_id)
            context = self.context
            if context:
                asyncio.create_task(context.remove_incoming_connection(self.cxn_id))
                # await context.remove_incoming_connection(self.cxn_id)
        finally:
            self._state = None

    async def on_message(self, message):
        asyncio.create_task(self.state.on_message(message))


#
# Main Context Objects
#
class WsContext(object):
    """Context that stores the current connections for the app."""

    def __init__(self, json_route_mapping, other_parsers=None, debug=0):
        """Create a WsContext with the given JSON routes, and optional opcode parsers.

        JSON is always a parser for any WsContext and the routes are passed via:
            `json_route_mapping`
        Other types of messages are also possible, and are registered by creating a
        'parser' class and adding it to the `opcode_mapping` dict with the single-byte
        opcode: <opcode> -> Custom Parser
        """
        # Store any custom opcode mappings here.
        # By default, this will assume JSON messages, which are implicitly
        # handled via the 'route_mapping' parameter above. To parse fields
        # other than JSON, add to this mapping here.
        other_parsers = other_parsers or [RawProxyParser()]
        self.opcode_mapping = {
            parser.opcode: parser
            for parser in other_parsers
        }
        if JsonParser.opcode in self.opcode_mapping:
            msg = "Opcode {} (the '{{' character) conflicts with JSON requests!".format(
                JsonParser.opcode)
            raise Exception(msg)
        self.opcode_mapping[JsonParser.opcode] = JsonParser(json_route_mapping)

        # Condition that should be notified whenever the client count changes.
        self._cond = asyncio.Condition()

        # Stores connection requests from clients.
        # In other words, connections initiated remotely and connecting here.
        self._cxn_mapping = {}
        # Stores outgoing connections to clients.
        # In other words, connection initiated here and going out.
        self._out_mapping = {}
        # Store the debug level.
        self._debug = debug

    async def add_incoming_connection(self, cxn_id, cxn, other_url=None):
        """Create the new connection and add it.

        Returns the WebsocketState for this connection.
        """
        # Create the WebsocketState
        state = WebsocketState(self, cxn, other_url=other_url, prefix='I')
        async with self._cond:
            self._cxn_mapping[cxn_id] = state
            self._cond.notify_all()
        return state

    async def remove_incoming_connection(self, cxn_id):
        async with self._cond:
            state = self._cxn_mapping.pop(cxn_id, None)
            self._cond.notify_all()
        if state:
            await state.close()

    async def add_outgoing_connection(self, cxn_id, cxn, other_url=None):
        state = WebsocketState(self, cxn, other_url=other_url, prefix='O')
        async with self._cond:
            self._out_mapping[cxn_id] = state
        return state

    async def remove_outgoing_connection(self, cxn_id):
        async with self._cond:
            state = self._out_mapping.pop(cxn_id, None)
        if state:
            await state.close()

    @property
    def incoming_mapping(self):
        """Return the mapping of: incoming cxn ID -> WebsocketState."""
        return self._cxn_mapping

    @property
    def outgoing_mapping(self):
        """Return the mapping of: outgoing cxn ID -> WebsocketState."""
        return self._out_mapping

    @property
    def debug_enabled(self):
        """Return if debugging is enabled for this context."""
        return self._debug > 0

    @property
    def debug(self):
        return self._debug

    async def wait_for_connection_change(self):
        async with self._cond:
            await self._cond.wait()


class WebsocketState(object):
    """Class that groups a websocket connection and context.

    This implements the details of messages, and potential state for
    the connection. It supplies proxy methods for writing to the websocket,
    with some message ID.
    """

    def __init__(self, context, connection, other_url=None, prefix='N'):
        self._context = weakref.ref(context)
        self._connection = connection
        self._other_url = other_url
        self._prefix = prefix
        self._next_id = 0
        
        # Outgoing messages that are initiated locally. Stored as a map of:
        # msg_id -> handler(res) callbacks.
        self._out_mapping = dict()
        
        # Store a mapping of sockets that are being proxied/forwarded.
        self._socket_mapping = dict()

    def add_proxy_socket(self, socket_id, handler):
        self._socket_mapping[socket_id] = handler

    def remove_proxy_socket(self, socket_id):
        self._socket_mapping.pop(socket_id, None)

    @property
    def cxn_id(self):
        if self.connection:
            return self.connection.cxn_id
        return None

    @property
    def debug(self):
        """Return the debug level for this websocket.

        NOTE: Debug is much slower, but will print received messages and so forth
        to the logger and other sources as configured.
        """
        return self.context.debug or 0

    @property
    def socket_mapping(self):
        """Return the mapping of sockets this context is currently forwarding."""
        return self._socket_mapping
    
    @property
    def other_url(self):
        if not self._other_url:
            return "(not known)"
        return self._other_url

    @property
    def context(self):
        return self._context()

    @property
    def connection(self):
        return self._connection

    @property
    def msg_mapping(self):
        return self._out_mapping

    def get_next_id(self):
        self._next_id += 1
        return u"{}{}".format(self._prefix, self._next_id)
    
    def get_info(self):
        return {
            "url": self.other_url
        }

    async def write_message(self, msg, binary=False):
        if self.debug > 0:
            logger.debug("%s sending %s bytes.", self.cxn_id, len(msg))
        if self.debug > 1:
            logger.debug("%s SEND: %s", self.cxn_id, msg)
        try:
            if isinstance(msg, dict):
                msg = json.dumps(msg)
            # HACK FOR NOW: 'cxn.write_message()' typically only supports inputs of
            # type: bytes, str, dict. Curiously, memoryview and bytearray are omitted.
            # So, cast it here explicitly, at the expense (possibly) of another copy
            # operation.
            elif isinstance(msg, (bytearray, memoryview)):
                # Binary MUST be true if the data is passed as such. If this case
                # is detected, force it True regardless of above.
                msg = bytes(msg)
                binary = True

            cxn = self.connection
            if cxn:
                await cxn.write_message(msg, binary=binary)
        except websocket.WebSocketClosedError:
            logger.debug("Cxn ID %s: Dropping 'write' to closed websocket!", self.cxn_id)
        except Exception:
            logger.exception("Unexpected exception writing to cxn_id: %s", self.cxn_id)
    
    async def on_message(self, message):
        # Parse the message according to the context route handling.
        if not message:
            return

        # Parse the protocol here by parsing the first byte. If the first byte is
        # NOT of type: 'bytes', encode it via UTF8.
        if isinstance(message, str):
            opcode = ord(message[0].encode('utf8'))
        else:
            opcode = message[0]
        if not isinstance(opcode, int):
            opcode = ord(opcode)

        parser = self.context.opcode_mapping.get(opcode)
        if not parser:
            logger.warning("For Connection (ID: %s): Opcode '%s' is not supported!",
                           self.cxn_id, opcode)
            return
        try:
            # Each parser must accept two arguments:
            # 1. The WebsocketState object (with the connection/context to write
            #    messages to and store any state.
            # 2. The (whole) message to process.
            #
            # The parser should also handle any exceptions internally; if some
            # Exception surfaces here, it will be logged, but no response sent.
            asyncio.create_task(parser.process_message(self, message))
        except Exception:
            logger.exception("Uncaught exception processing message!")

    async def close(self):
        # Invoke the 'close' call on any messages that are currently pending,
        # assuming it is supported.
        res = []
        for handler in self.msg_mapping.values():
            if hasattr(handler, 'close'):
                # handler.close() should return a future.
                res.append(handler.close())
        await asyncio.gather(*res)


class Endpoint(object):

    def __init__(self, state, msg_id):
        self.msg_id = msg_id
        self.state = state
    
    async def next(self, msg):
        await self.state.write_message(dict(
            id=self.msg_id, status="next", message=msg
        ))

    async def complete(self):
        await self.state.write_message(dict(
            id=self.msg_id, status="complete"
        ))

    async def error(self, error):
        await self.state.write_message(dict(
            id=self.msg_id, status="error", message=error
        ))

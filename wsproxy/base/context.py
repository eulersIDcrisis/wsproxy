"""server_context.py.

Implements the server context that holds all of the open servers
for this tunnel.
"""
import json
import uuid
import weakref
import asyncio
from enum import Enum
from functools import partial
from tornado import tcpserver, tcpclient, websocket, ioloop


class WsContext(object):
    """Server Context that stores the current connections to the server."""

    def __init__(self, opcode_mapping=None):
        self._cxn_mapping = {}

        # Store any custom opcode mappings here.
        # By default, this will assume JSON messages, which are implicitly
        # handled via the 'route_mapping' parameter above. To parse fields
        # other than JSON, add to this mapping here.
        self.opcode_mapping = opcode_mapping or {}

    def add_connection(self, cxn_id, cxn):
        """Create the new connection and add it."""
        # Create the WebsocketState
        cxn = WebsocketState(self, cxn)
        self._cxn_mapping[cxn_id] = cxn
        return cxn
    
    def remove_connection(self, cxn_id):
        cxn = self._cxn_mapping.pop(cxn_id, None)
        if cxn:
            cxn.close()


class JsonWsContext(WsContext):
    """Websocket context that listens for JSON message requests by default.

    This accepts a list of routes to receive and process.
    """
    def __init__(self, route_mapping, other_opcode_mapping=None):
        opcode_mapping = other_opcode_mapping or dict()
        if '{' in opcode_mapping:
            msg = "Opcode {} (the '{{' character) conflicts with JSON requests!".format(ord('{'))
            raise Exception(msg)
        opcode_mapping['{'] = JsonParser(route_mapping)
        super(JsonWsContext, self).__init__(opcode_mapping)


class WebsocketState(object):
    """Class that groups a websocket connection and context.

    This implements the details of messages, and potential state for
    the connection. It supplies proxy methods for writing to the websocket,
    with some message ID.
    """

    def __init__(self, context, connection):
        self._context = weakref.ref(context)
        self._connection = connection
        self.msg_mapping = dict()

    @property
    def context(self):
        return self._context()

    @property
    def connection(self):
        return self._connection

    async def write_message(self, msg):
        cxn = self.connection
        if cxn:
            await cxn.write_message(msg)
    
    async def on_message(self, message):
        # Parse the message according to the context route handling.
        if not message:
            return
        logger.info("MESSAGE: %s", message)

        # Parse the protocol here by parsing the first byte.
        opcode = message[0]
        parser = self.context.opcode_mapping.get(opcode)
        if not parser:
            logger.warn("Opcode '%s' (int value: %d) is not supported!", opcode, ord(opcode))
            return
        try:
            # Each parser must accept two arguments:
            # 1. The WebsocketState object (with the connection/context to write
            #    messages to and store any state.
            # 2. The (whole) message to process.
            #
            # The parser should also handle any exceptions internally; if some
            # Exception surfaces here, it will be logged, but no response sent.
            await parser.process_message(self, message)
        except Exception:
            logger.exception("Uncaught exception processing message!")

    def close(self):
        pass


class LocalTCPServer(tcpserver.TCPServer):
    
#     @classmethod
#     def create(cls, ws, port=10000):
#         res = cls(ws, port=port)
#         res.listen(port)
#         return res

    def __init__(self, remote_port, local_port=9000, ws=None):
        super(LocalTCPServer, self).__init__()

        # For now, it is okay to just use an incrementing ID, since this
        # should always run in the IOLoop.
        self._count = 0

        self.stream_mapping = {}
        self.remote_port = remote_port
        self.local_port = local_port
        self.bind(self.local_port)

        self.ws = ws
    
    def _get_next_count(self):
        self._count += 1
        if self._count > (2**31):
            self._count = 1
        return self._count

    async def handle_stream(self, stm, addr):
        stm_id = self._get_next_count()
        msg_id = self._get_next_count()
        try:
            # Send the new connection over the websocket. The protocol to
            # open a connection is: 0 | <msg_id> | <remote port> | <stm_id>
            data = struct.pack('!BIHI', 0, msg_id, self.remote_port, stm_id)
            await self.ws.write_message(data)
            
            # Result should be something of the form:
            # <msg_id> | <0|1>
            res = await self.ws.wait_for_message(msg_id, timeout=10)
            
            # Should we await a response that the connection is open?
            # For now, we'll just blindly tunnel the data.
            self.stream_mapping[stm_id] = stm
            data = await stm.read_bytes(1024, partial=True)
            main_logger.info("DATA ON STREAM: %s", data)

        except iostream.StreamClosedError:
            pass
        finally:
            self.stream_mapping.pop(stm_id, None)

    def close(self):
        self.stop()
        for stm in stream_mapping.values():
            stm.close()

#
# JSON Parsing Utilities
#
class RouteType(Enum):
    POST = 'post'
    ONCE = 'once'
    SUB = 'subscribe'
    UNSUB = 'unsubscribe'


class Route(object):
    def __init__(self, r_type, route_name, handler):
        self._route_type = RouteType(r_type)
        self._route_name = route_name
        self._handler = handler
    
    @property
    def name(self):
        return self._route_name
    
    async def __call__(self, response_proxy, args):
        return await self._handler(response_proxy, args)


class JsonResponseProxy(object):

    def __init__(self, state, msg_id):
        self.msg_id = msg_id
        self.state = state
    
    async def next(self, msg, with_complete=True):
        await self.state.write_message(dict(
            id=self.msg_id, status="next", message=msg
        ))
        if with_complete:
            await self.complete()

    async def complete(self):
        await self.state.write_message(dict(
            id=self.msg_id, status="complete"
        ))

    async def error(self, error):
        await self.state.write_message(dict(
            id=self.msg_id, status="error", message=error
        ))


class JsonParser(object):
    """Basic Parser for JSON-style routes.
    
    To make development easier (and consistent with RxJS), the following types
    of routes are supported, followed by possible responses (all states will
    return an error code, if that is more applicable):
     - post: respond 'next' -> 'complete'
     - once: respond 'next' -> 'complete'
     - subscribe: respond 'next' repeatedly until unsubscribed or closed.
            The server will send 'complete' to close the sub, either because
            an 'unsubscribe' was received from the client, or because this
            subscription has no further updates.
     - unsubscribe: Request to unsubscribe from an existing subscription.
    
    After the relevant fields are parsed from the JSON, this will call the handler
    with the given arguments:
    """
    def __init__(self, route_list):
        self.__route_mapping = {
            route.name: route
            for route in route_list
        }
    
    @property
    def route_mapping(self):
        return self.__route_mapping

    async def process_message(self, state, message):
        error = None
        try:
            res = json.loads(message)
            route = res['route']
            msg_id = res['id']
            existing_handler = state.msg_mapping.get(msg_id)
            if existing_handler:
                existing_handler.on_update(state, message)
                return
            try:
                msg_type = RouteType(res['type'])
            except Exception:
                raise ValueError("Invalid message type: {}".format(res.get('type', '(not set)')))

            args = res.get('args')
            
            # If we get here, we have enough fields to run the route, so do it.
            response_proxy = JsonResponseProxy(state, msg_id)

            # Check if the route already exists and invoke that handler if so.
            # Don't worry about popping this route from 'msg_mapping' here; it is
            # the responsibility whoever created the route in the first place to
            # handle this.
            handler = state.msg_mapping.get(msg_id)
            if handler:
                await handler(response_proxy, args)
                return

            # Otherwise, we should invoke a new handler from scratch.
            handler = self.route_mapping.get(route)
            if not handler:
                raise Exception("Invalid route!")

            # Otherwise, create the route, await it, then run it. The responsibility
            # of registering the route, then popping it when done happens here.
            try:
                callback = partial(handler, response_proxy)
                state.msg_mapping[msg_id] = callback
                await callback(args)
                return
            except Exception:
                logger.exception("Uncaught error in handler! Type: %s Route: %s Msg ID: %s",
                                 msg_type.value, route, msg_id)
                await response_proxy.error("Internal Error")
                await response_proxy.complete()
            finally:
                state.msg_mapping.pop(msg_id, None)

            error = "Internal Error"
        except KeyError as exc:
            error = u'Missing field: {}'.format(exc)
        except Exception as exc:
            logger.error(u"%s", exc)
            error = "Bad Arguments!"
        await state.write_message({
            'status': 'error',
            'message': error
        })

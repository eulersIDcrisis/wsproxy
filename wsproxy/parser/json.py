"""json.py.

Module to implement the parsing logic for JSON message types
on the websocket connection.
"""
import json
import asyncio
from enum import Enum
from tornado import websocket
from wsproxy.auth_manager import NotAuthorized
from wsproxy.util import main_logger as logger


class SubscriptionError(Exception):
    """Exception denoting an error in the subscription."""


class SubscriptionComplete(SubscriptionError):
    """Exception denoting that a subscription is complete."""


#
# JSON Parsing Utilities
#
class RouteType(Enum):
    POST = 'post'
    ONCE = 'once'
    SUB = 'subscribe'
    UNSUB = 'unsubscribe'


class Route(object):
    def __init__(self, r_type, route_name, handler, category='other'):
        self._route_type = RouteType(r_type)
        self._route_name = route_name
        self._handler = handler
        self._category = category

    @property
    def route_type(self):
        return self._route_type

    @property
    def name(self):
        return self._route_name

    @property
    def category(self):
        """Return the category for this route.

        Used to determine if this route is valid in some cases.
        """
        return self._category

    async def __call__(self, endpoint, args=None):
        """Run the route asynchronously."""
        try:
            await self._handler(endpoint, args)
        except SubscriptionComplete:
            return
        except Exception:
            logger.exception("Unexpected exception!")
        finally:
            await endpoint.complete()
            endpoint.state.msg_mapping.pop(endpoint.msg_id, None)


class Endpoint(object):

    def __init__(self, state, msg_id, complete_on_next=True):
        self.state = state
        self.msg_id = msg_id
        self.complete_on_next = complete_on_next
        self._complete_called = False

    async def next(self, msg):
        await self.state.write_message(dict(
            id=self.msg_id, status="next", message=msg
        ))
        if self.complete_on_next:
            raise SubscriptionComplete()

    async def complete(self):
        if self._complete_called:
            return
        self._complete_called = True
        await self.state.write_message(dict(
            id=self.msg_id, status="complete"
        ))

    async def error(self, error):
        await self.state.write_message(dict(
            id=self.msg_id, status="error", message=error
        ))
        if self.complete_on_next:
            raise SubscriptionComplete()


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

    After the relevant fields are parsed from the JSON, this will call the
    handler with the given arguments:
    """
    # A single-byte character to identify JSON routes. Here, the opcode
    # is clearly the first character of a JSON object.
    opcode = ord('{')

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
        except Exception:
            logger.error("Bad Arguments: Could not parse JSON!")
            return
        if state.debug > 0:
            logger.debug("%s JSON recv: %s", state.cxn_id, res)

        # Parse the msg_id, required for all messages. If not included, just
        # drop the message.
        try:
            msg_id = res['id']
        except KeyError as exc:
            if state.debug > 0:
                logger.debug("Drop message with missing field '%s': %s",
                             exc, res)
            return
        try:
            # If this is the response from some outgoing message, this should
            # have an explicit 'status' field, set to one of: 'next',
            # 'complete', 'error'.
            # If it is 'None', then this cannot be the response from an
            # outgoing message.
            status = res.get('status')
            if status:
                handler = state.msg_mapping.get(msg_id)
                if handler:
                    await handler(res)
                    return
                # Otherwise, this message is malformed. Just drop it, with an
                # optional warning.
                logger.warning("Dropping received message. ID: %s Status: %s",
                               msg_id, status)
                return
        except Exception:
            logger.exception("Error handling received message!")
            return

        # Parse the message type.
        try:
            # Otherwise, assume that this is an incoming message that should
            # now be initiated. Determine the type and route appropriately.
            msg_type_str = res['type']
            msg_type = RouteType(msg_type_str)
        except Exception:
            await state.write_message(dict(
                status="error", message="Missing or invalid field: 'type'"
            ))
            return

        try:
            # First, check if the message type is of `UNSUB`, which basically
            # means to close the handler (if applicable), then remove it from
            # the list.
            #
            # IMPORTANT: By cancelling the asyncio task, it will not execute.
            # This means that we need to manually send the 'complete' message.
            if msg_type == RouteType.UNSUB:
                try:
                    # Cancel this sub, if applicable.
                    sub, endpoint = state.msg_mapping.pop(msg_id, (None, None))
                    if sub:
                        sub.cancel()
                        # NOTE: This no-ops if the handler already sent the
                        # 'complete' message; this check is necessary because
                        # it is possible to cancel the handler before it is
                        # even scheduled to run, in which case we should still
                        # respond with a 'complete'.
                        await endpoint.complete()
                except Exception:
                    logger.exception(
                        "Unexpected exception processing msg_id: %s", msg_id)
                # Done processing this message.
                return

            # For the other route types, there will be a "route" and optional
            # "args" field.
            route = res['route']
            args = res.get('args')

            if state.msg_mapping.get(msg_id):
                raise Exception(
                    "Existing handler already exists with Msg ID: %s",
                    msg_id)

            handler = self.route_mapping[route]
            endpoint = Endpoint(
                state, msg_id,
                complete_on_next=bool(msg_type != RouteType.SUB))
            sub = asyncio.create_task(handler(endpoint, args))
            # sub = asyncio.create_task(handler(state, msg_id, msg_type, args))
            state.msg_mapping[msg_id] = (sub, endpoint)

            # Will raise a not authorized exception if invalid.
            state.auth_context.check_json_route(route, res.get('auth'))

            return
        except websocket.WebSocketClosedError:
            logger.error("Websocket closed")
            return
        except NotAuthorized as exc:
            error = u"Not authorized!"
        except KeyError as exc:
            error = u'Missing field: {}'.format(exc)
        except ValueError as exc:
            error = u'Invalid argument: {}'.format(exc)
        except Exception as exc:
            logger.exception(u"%s", exc)
            error = "Bad Arguments!"
        await state.write_message({
            'status': 'error',
            'message': error
        })


class BaseRequest(object):

    def __init__(self, state, msg_type, route, args, msg_id=None):
        self.state = state
        self.msg_type = msg_type.value
        self.msg_id = msg_id if msg_id else state.get_next_id()
        self.route = route
        self.args = args

        self._response_queue = asyncio.Queue()
        self._closed = asyncio.Event()

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, tb):
        await self.close()

    async def open(self):
        self.state._out_mapping[self.msg_id] = self.handle_response
        await self.state.write_message(dict(
            type=self.msg_type,
            id=self.msg_id,
            route=self.route,
            args=self.args
        ))

    async def close(self):
        self._closed.set()
        self.state._out_mapping.pop(self.msg_id, None)

    async def receive(self):
        if self._closed.is_set():
            raise SubscriptionComplete()
        msg = await self._response_queue.get()
        # LAME: Python's queue interface really sucks; when consuming from a
        # queue, call 'task_done' to imply that it has been processed.
        self._response_queue.task_done()
        status = msg['status']
        if status == 'next':
            return msg['message']
        elif status == 'complete':
            raise SubscriptionComplete()
        elif status == 'error':
            raise SubscriptionError(msg['message'])
        else:
            logger.error("Invalid message: %s", msg)
            raise ValueError("Invalid message!")

    async def handle_response(self, msg):
        await self._response_queue.put(msg)


async def once(state, route, args=None):
    """Send a 'once' request to the other endpoint.

    Returns a Future that resolves with the results, or raises if the
    result was an error.

    A 'once' request should respond with either:
     - 'next' -> 'complete'
     - 'error' -> 'complete'
    """
    request = BaseRequest(state, RouteType.ONCE, route, args)
    async with request:
        res = None
        try:
            res = await request.receive()
            error = await request.receive()
            raise Exception("Unexpected response: {}".format(error))
        except SubscriptionComplete:
            return res


async def post(state, route, args=None):
    """Send a 'post' request to the other endpoint.

    Returns a Future that resolves with the results, or raises if the
    result was an error.

    A 'post' request (like a 'once') should respond with either:
     - 'next' -> 'complete'
     - 'error' -> 'complete'
    """
    request = BaseRequest(state, RouteType.POST, route, args)
    async with request:
        try:
            res = await request.receive()
            error = await request.receive()
            raise Exception("Unexpected response: {}".format(error))
        except SubscriptionComplete:
            return res


class Subscription(BaseRequest):
    """Class that returns a handle to a subscription.

    This object has methods that will iterate over the next results
    for the subscription.
    """

    def __init__(self, state, route, args, msg_id=None):
        super(Subscription, self).__init__(state, RouteType.SUB, route, args)

    async def result_generator(self):
        try:
            while True:
                update = await self.next()
                yield update
        except (SubscriptionComplete):
            self._closed.set()
            return

    async def next(self, timeout=None):
        if timeout:
            return await asyncio.wait_for(self.receive(), timeout=timeout)
        else:
            return await self.receive()

    async def close(self):
        if self._closed.is_set():
            return
        await self.state.write_message(dict(
            id=self.msg_id, type=RouteType.UNSUB.value))


def setup_subscription(state, route, args=None):
    """Set to the given route.

    Returns an asynchronous generator where each iterated element is the next
    result received from the endpoint.

    A 'subscribe' request should respond with:
     - 'next' -> 'complete'
     - 'next' -> 'next' -> ... 'complete'
     - 'error' -> 'complete'
    """
    return Subscription(state, route, args)

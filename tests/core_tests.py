"""core_tests.py.

Test cases for the basic websocket state and connection routes.
"""
import unittest
import logging
from tornado import ioloop, web, websocket, testing
# Local imports
from wsproxy.auth import BasicPasswordAuthManager, AuthContext
from wsproxy.core import (
    WsContext, WebsocketState, WsServerHandler, WsClientConnection
)
import wsproxy.parser.json as json_request
from wsproxy.routes import info as info_routes
# Testing imports
from tests import debug_util


class WebsocketServerTest(testing.AsyncHTTPTestCase):

    def get_app(self):
        manager = BasicPasswordAuthManager('user', 'random')
        auth_context = AuthContext(manager, dict(user=manager))
        route_mapping = {
            route.name: route
            for route in info_routes.get_routes()
        }
        self.context = WsContext(
            auth_context, route_mapping, debug=debug_util.get_unittest_debug())
        self.client_context = WsContext(
            auth_context, route_mapping, debug=debug_util.get_unittest_debug())

        return web.Application([
            (r'/', WsServerHandler, dict(context=self.context)),
            # (r'/client/details', InfoHandler, dict(context=self.context)),
            # (r'/client/(?P<cxn_id>[^/]+)', ClientInfoHandler, dict(context=self.context))
        ])

    async def ws_connect(self, protocol='ws', path='/'):
        url = "{}://127.0.0.1:{}{}".format(protocol, self.get_http_port(), path)

        cxn = WsClientConnection(self.client_context, url)
        state = await cxn.open()
        return state

    @testing.gen_test
    async def test_basic_once(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        res = await json_request.once(state, 'echo', "Hello")
        self.assertEqual("Hello", res)

    @testing.gen_test
    async def test_basic_subscription(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        # This handler counts down to 0. The messages should start at 3 and end at 0.
        expected = 3
        sub = json_request.setup_subscription(state, 'count', dict(count=3, timeout=0.001))
        async with sub:
            async for msg in sub.result_generator():
                self.assertFalse(expected < 0)
                self.assertEqual(expected, msg)
                expected -= 1

        # Try again, with a long timeout, but unsubscribe first.
        sub = json_request.setup_subscription(state, 'count', dict(count=3, timeout=10))
        async with sub:
            await sub.close()
            async for msg in sub.result_generator():
                self.fail("Should not return any messages, because this was unsubscribed.")

        # Try yet again, with a short timeout, and a few calls before unsubscribing.
        sub = json_request.setup_subscription(state, 'count', dict(count=5, timeout=0.001))
        async with sub:
            count = 5
            async for msg in sub.result_generator():
                self.assertEqual(count, msg)
                if count > 3:
                    count -= 1
                else:
                    await sub.close()
            self.assertEqual(3, count)
            self.assertEqual(3, msg)

    @testing.gen_test
    async def test_basic_subscription_once(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        res = await json_request.once(state, 'count', dict(count=3))
        self.assertEqual(3, res)

    @testing.gen_test
    async def test_multiple_subscriptions(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        sub1 = json_request.setup_subscription(state, 'count', dict(count=2, timeout=0.001))
        sub2 = json_request.setup_subscription(state, 'count', dict(count=2, timeout=0.001))
        async with sub1, sub2:
            msg1 = await sub1.next()
            self.assertEqual(2, msg1)
            msg1 = await sub1.next()
            self.assertEqual(1, msg1)
            msg2 = await sub2.next()
            self.assertEqual(2, msg2)
            msg2 = await sub2.next()
            self.assertEqual(1, msg2)


if __name__ == '__main__':
    # debug_util.enable_debug()
    unittest.main()

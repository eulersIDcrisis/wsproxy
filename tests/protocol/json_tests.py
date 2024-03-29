"""json_tests.py.

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
import wsproxy.protocol.json as json_protocol
from wsproxy.routes import info as info_routes
# Testing imports
from tests import testing_utils


class WebsocketServerTest(testing_utils.AsyncWsproxyTestCase):

    @testing.gen_test
    async def test_basic_once(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        res = await json_protocol.once(state, 'echo', "Hello")
        self.assertEqual("Hello", res)

    @testing.gen_test
    async def test_basic_subscription(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        # This handler counts down to 0. The messages should start at 3 and end at 0.
        expected = 3
        sub = json_protocol.setup_subscription(state, 'count', dict(count=3, timeout=0.001))
        async with sub:
            async for msg in sub.result_generator():
                self.assertFalse(expected < 0)
                self.assertEqual(expected, msg)
                expected -= 1

        # Try again, with a long timeout, but unsubscribe first.
        sub = json_protocol.setup_subscription(state, 'count', dict(count=3, timeout=10))
        async with sub:
            await sub.close()
            async for msg in sub.result_generator():
                self.fail("Should not return any messages, because this was unsubscribed.")

        # Try yet again, with a short timeout, and a few calls before unsubscribing.
        sub = json_protocol.setup_subscription(state, 'count', dict(count=5, timeout=0.001))
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

        res = await json_protocol.once(state, 'count', dict(count=3))
        self.assertEqual(3, res)

    @testing.gen_test
    async def test_multiple_subscriptions(self):
        state = await self.ws_connect()
        self.assertIsNotNone(state)

        sub1 = json_protocol.setup_subscription(state, 'count', dict(count=2, timeout=0.001))
        sub2 = json_protocol.setup_subscription(state, 'count', dict(count=2, timeout=0.001))
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
    verbosity = testing_utils.unittest_setup()
    unittest.main(verbosity=verbosity)

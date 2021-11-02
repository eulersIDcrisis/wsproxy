"""proxy_tests.py.

Tests for the wsproxy.protocol.proxy module.
"""
import unittest
import uuid
from contextlib import AsyncExitStack
from tornado import testing
# Local imports
from wsproxy.protocol import (
    proxy, json as json_protocol
)

# Test imports
from tests import testing_utils


class ProxyBufferTest(testing.AsyncTestCase):

    @testing.gen_test
    async def test_proxy_buffer(self):
        # Create a buffer with 4 bytes for simple testing.
        buffer = proxy.ProxyBuffer(5)
        bytes_written = await buffer.enqueue(b'abcd')
        self.assertEqual(4, bytes_written)
        total = buffer.get_total_bytes_enqueued()
        self.assertEqual(4, total)
        total = buffer.get_total_bytes_dequeued()
        self.assertEqual(0, total)
        size = buffer.get_current_count()
        self.assertEqual(4, size)

        data = bytearray(2)
        bytes_read = await buffer.dequeue(data)
        self.assertEqual(2, bytes_read)
        self.assertEqual(b'ab', bytes(data))
        total = buffer.get_total_bytes_enqueued()
        self.assertEqual(4, total)
        total = buffer.get_total_bytes_dequeued()
        self.assertEqual(2, total)
        size = buffer.get_current_count()
        self.assertEqual(2, size)

        bytes_read = await buffer.dequeue(data)
        self.assertEqual(2, bytes_read)
        self.assertEqual(b'cd', bytes(data))
        total = buffer.get_total_bytes_enqueued()
        self.assertEqual(4, total)
        total = buffer.get_total_bytes_dequeued()
        self.assertEqual(4, total)
        size = buffer.get_current_count()
        self.assertEqual(0, size)

        bytes_written = await buffer.enqueue(b'efg')
        self.assertEqual(3, bytes_written)
        total = buffer.get_total_bytes_enqueued()
        self.assertEqual(7, total)
        total = buffer.get_total_bytes_dequeued()
        self.assertEqual(4, total)
        size = buffer.get_current_count()
        self.assertEqual(3, size)

        bytes_read = await buffer.dequeue(data)
        self.assertEqual(2, bytes_read)
        self.assertEqual(b'ef', data)
        total = buffer.get_total_bytes_enqueued()
        self.assertEqual(7, total)
        total = buffer.get_total_bytes_dequeued()
        self.assertEqual(6, total)
        size = buffer.get_current_count()
        self.assertEqual(1, size)

        bytes_read = await buffer.dequeue(data)
        self.assertEqual(1, bytes_read)
        self.assertEqual(b'g', data[0:1])
        total = buffer.get_total_bytes_enqueued()
        self.assertEqual(7, total)
        total = buffer.get_total_bytes_dequeued()
        self.assertEqual(7, total)
        size = buffer.get_current_count()
        self.assertEqual(0, size)


#
# Proxy Subscription Tests and Utilities.
#
class BufferedProxyContext(proxy.RawProxyContext):

    def __init__(self, state, socket_id):
        super(BufferedProxyContext, self).__init__(
            state, socket_id)
        self._read_buffer = proxy.ProxyBuffer(1024)
        self._write_buffer = proxy.ProxyBuffer(1024)

    async def _read_from_local(self, buff):
        return await self._read_buffer.dequeue(buff)

    async def _write_to_local(self, data):
        await self._write_buffer.enqueue(data)


class ProxySubscriptionTests(testing_utils.AsyncWsproxyTestCase):

    @testing.gen_test
    async def test_proxy_subscription(self):
        socket_id = uuid.uuid1()
        async with AsyncExitStack() as exit_stack:
            remote_sock, remote_port = testing.bind_unused_port()
            remote_server = testing_utils.EchoServer()
            remote_server.add_sockets([remote_sock])
            # Register the remote server to stop on exit.
            exit_stack.callback(remote_server.stop)

            # Make a request to proxy traffic from a local port to the remote
            # server.
            state = await self.ws_connect()

            async with AsyncExitStack() as sub_stack:
                local_proxy = BufferedProxyContext(state, socket_id)
                await sub_stack.enter_async_context(local_proxy)

                sub = json_protocol.setup_subscription(
                    state, "proxy_socket", dict(
                        host="localhost", port=remote_port, protocol="tcp",
                        socket_id=socket_id.hex, buffsize=1)
                )
                await sub_stack.enter_async_context(sub)
                msg = await sub.next()
                self.assertIn('connection_status', msg)
                self.assertEqual('connected', msg['connection_status'])
                self.assertIsNotNone(msg['socket_id'])
                self.assertEqual(0, msg['count'])


if __name__ == '__main__':
    verbosity = testing_utils.unittest_setup()
    unittest.main(verbosity=verbosity)

"""proxy_tests.py.

Tests for the wsproxy.protocol.proxy module.
"""
import unittest
from tornado import testing
# Local imports
from wsproxy.protocol import proxy

# Test imports
from tests import testing_utils


class ProxyBufferTest(testing.AsyncTestCase):

    @testing.gen_test
    async def test_proxy_buffer(self):
        # Create a buffer with 4 bytes for simple testing.
        buffer = proxy.ProxyBuffer(5)
        bytes_written = await buffer.enqueue(b'abcd')
        self.assertEqual(4, bytes_written)
        total = await buffer.get_total_bytes_enqueued()
        self.assertEqual(4, total)
        total = await buffer.get_total_bytes_dequeued()
        self.assertEqual(0, total)
        size = await buffer.get_current_count()
        self.assertEqual(4, size)

        data = bytearray(2)
        bytes_read = await buffer.dequeue(data)
        self.assertEqual(2, bytes_read)
        self.assertEqual(b'ab', bytes(data))
        total = await buffer.get_total_bytes_enqueued()
        self.assertEqual(4, total)
        total = await buffer.get_total_bytes_dequeued()
        self.assertEqual(2, total)
        size = await buffer.get_current_count()
        self.assertEqual(2, size)

        bytes_read = await buffer.dequeue(data)
        self.assertEqual(2, bytes_read)
        self.assertEqual(b'cd', bytes(data))
        total = await buffer.get_total_bytes_enqueued()
        self.assertEqual(4, total)
        total = await buffer.get_total_bytes_dequeued()
        self.assertEqual(4, total)
        size = await buffer.get_current_count()
        self.assertEqual(0, size)

        bytes_written = await buffer.enqueue(b'efg')
        self.assertEqual(3, bytes_written)
        total = await buffer.get_total_bytes_enqueued()
        self.assertEqual(7, total)
        total = await buffer.get_total_bytes_dequeued()
        self.assertEqual(4, total)
        size = await buffer.get_current_count()
        self.assertEqual(3, size)

        bytes_read = await buffer.dequeue(data)
        self.assertEqual(2, bytes_read)
        self.assertEqual(b'ef', data)
        total = await buffer.get_total_bytes_enqueued()
        self.assertEqual(7, total)
        total = await buffer.get_total_bytes_dequeued()
        self.assertEqual(6, total)
        size = await buffer.get_current_count()
        self.assertEqual(1, size)

        bytes_read = await buffer.dequeue(data)
        self.assertEqual(1, bytes_read)
        self.assertEqual(b'g', data[0:1])
        total = await buffer.get_total_bytes_enqueued()
        self.assertEqual(7, total)
        total = await buffer.get_total_bytes_dequeued()
        self.assertEqual(7, total)
        size = await buffer.get_current_count()
        self.assertEqual(0, size)


if __name__ == '__main__':
    verbosity = testing_utils.unittest_setup()
    unittest.main(verbosity=verbosity)

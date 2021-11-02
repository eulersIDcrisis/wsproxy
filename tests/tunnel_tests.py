"""core_tests.py.

Test cases for the basic websocket state and connection routes.
"""
import unittest
import uuid
import asyncio
import contextlib
from tornado import ioloop, web, websocket, tcpserver, iostream, testing
from tornado.httpclient import AsyncHTTPClient
# Local imports
from wsproxy.core import WsClientConnection
from wsproxy.protocol import json as json_request
from wsproxy.protocol import proxy
from wsproxy.routes import tunnel

# Test imports
from tests import testing_utils


# def prepare_curl_socks5(curl):
#     import pycurl
#     curl.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS5)


class BufferedRawProxyContext(tunnel.RawProxyContext):

    def __init__(self):
        super().__init__()
        self._cond = asyncio.Condition()
        self._received_data = bytearray()
        self._sent_data = bytearray()
        self._stop_event = asyncio.Event()

    async def _write_to_local(self, data):
        # This is confusing, but basically this is called when data is received
        # from the remote source and should be written out to the local socket.
        async with self._cond:
            self._received_data.append(data)
            # Notify that some data was received.
            self._cond.notify_all()

    async def _read_from_local(self, buff):
        # This is data that should be written out to the remote proxy.
        max_count = len(buff)
        async with self._cond:
            while not self._stop_event.is_set():
                if len(self._send_queue_data) <= 0:
                    self._cond.wait()
                    continue
            self._send_queue_data

    async def reset(self):
        async with self._cond:
            self._received_data = bytearray()
            self._send_queue_data = bytearray()
            self._cond.notify_all()

    async def write_data(self, data, wait_for_send=True):
        async with self._cond:
            self._send_queue_data.append(data)
            if not wait_for_send:
                return
            while not self._stop_event.is_set():
                if len(self._send_queue_data) <= 0:
                    return
                self._cond.wait()


class EchoServer(tcpserver.TCPServer):
    async def handle_stream(self, stream, address):
        try:
            data = await stream.read_until(b"\n")
            await stream.write(data)
        except iostream.StreamClosedError:
            return
        except Exception:
            traceback.print_exc()
            return
        finally:
            stream.close()


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


@unittest.skip('Not ready')
class WebsocketServerTest(testing_utils.AsyncWsproxyTestCase):

    @testing.gen_test
    async def test_proxy_socket(self):
        async with contextlib.AsyncExitStack() as exit_stack:
            # Setup a remote server.
            remote_sock, remote_port = testing.bind_unused_port()
            remote_server = EchoServer()
            remote_server.add_sockets([remote_sock])
            # Register the remote server to stop on exit.
            exit_stack.callback(remote_server.stop)

            # Make a request to proxy traffic from a local port to the remote
            # server.
            state = await self.ws_connect()

            socket_id = uuid.uuid1()
            async with json_request.setup_subscription(
                state, "proxy_socket", dict(
                    host="localhost", port=remote_port, protocol="tcp",
                    socket_id=socket_id.hex, buffsize=1)
            ) as sub:
                self.assertIn(sub.msg_id, state.msg_mapping)
                msg = await sub.next()
                self.assertIn('connection_status', msg)
                self.assertEqual('connected', msg['connection_status'])
                self.assertIsNotNone(msg['socket_id'])

                # The socket_id should be passed, to indicate the ID to used.
                socket_id = uuid.UUID(msg['socket_id'])
                remote_socket = tunnel_routes.TcpRemoteTunneledSocket(
                    state, socket_id)

                await remote_socket.send(b"Hello World\n")
                result = await remote_socket.receive()
                self.assertEqual(b"Hello World\n", result)

                # We are expecting a message that the socket has closed.
                msg = await sub.next()
                self.assertIn('connection_status', msg)
                self.assertEqual('closed', msg['connection_status'])

            # The sub should be closed. Assert that the socket was cleaned up.
            self.assertNotIn(sub.msg_id, state.msg_mapping)


if __name__ == '__main__':
    verbosity = testing_utils.unittest_setup()
    unittest.main(verbosity=verbosity)

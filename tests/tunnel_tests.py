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
from wsproxy.context import WsContext, WebsocketState
from wsproxy.connection import WsServerHandler, WsClientConnection
import wsproxy.protocol.json as json_request
import wsproxy.protocol.proxy as proxy_request
from wsproxy.routes import info as info_routes
from wsproxy.routes import tunnel as tunnel_routes

# Test imports
from tests import debug_util


def prepare_curl_socks5(curl):
    import pycurl
    curl.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS5)


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


class WebsocketServerTest(testing.AsyncHTTPTestCase):

    def get_app(self):
        routes = tunnel_routes.get_routes()
        self.context = WsContext(routes, other_parsers=[
            proxy_request.RawProxyParser()
        ], debug=debug_util.get_unittest_debug())

        self.client_context = WsContext(routes, other_parsers=[
            proxy_request.RawProxyParser()
        ], debug=debug_util.get_unittest_debug())

        return web.Application([
            (r'/', WsServerHandler, dict(context=self.context)),
        ])

    async def ws_connect(self, protocol='ws', path='/'):
        url = "{}://127.0.0.1:{}{}".format(
            protocol, self.get_http_port(), path)
        cxn = WsClientConnection(self.client_context, url)
        state = await cxn.open()
        return state

    @testing.gen_test
    async def test_proxy_route(self):
        with contextlib.ExitStack() as exit_stack:
            # Setup a remote server.
            remote_sock, remote_port = testing.bind_unused_port()
            remote_server = EchoServer()
            remote_server.add_sockets([remote_sock])
            # Register the remote server to stop on exit.
            exit_stack.callback(remote_server.stop)

            # Make a request to proxy traffic from a local port to the remote
            # server.
            state = await self.ws_connect()

            async with json_request.setup_subscription(
                state, "proxy_socket", dict(
                    host="localhost", port=remote_port, protocol="tcp")
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
    debug_util.enable_debug()
    unittest.main()

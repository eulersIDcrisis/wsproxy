"""testing_utils.py.

Module with some common testing utilities.
"""
import os
import logging
import unittest
from tornado import web, tcpserver, testing, iostream
from wsproxy.auth import AuthContext, BasicPasswordAuthManager
from wsproxy.core import WsContext, WsServerHandler, WsClientConnection
from wsproxy.protocol.proxy import RawProxyParser, get_routes as get_proxy_routes
from wsproxy.routes.registry import get_route_mapping


GLOBAL_DEBUG = 0
DEBUG_ENV_VARIABLE = 'WSPROXY_DEBUG'


def get_unittest_debug():
    if DEBUG_ENV_VARIABLE in os.environ:
        return int(os.environ[DEBUG_ENV_VARIABLE])
    return GLOBAL_DEBUG


def enable_debug(debug=1):
    global GLOBAL_DEBUG
    logging.basicConfig(level=logging.DEBUG)

    GLOBAL_DEBUG = debug


def unittest_setup():
    """Set up the debug levels and logging prior to running tests.

    Useful to call before running via 'unittest.main()'. This returns
    the expected keyword argument for "verbosity".
    """
    level = get_unittest_debug()
    if level > 0:
        enable_debug(debug=level)

    # NOTE: "verbosity" defaults to 1, not 0 for `unittests.main()`,
    # so floor the value to 1 here.
    if level <= 0:
        level = 1
    return level


# Placeholder object for default values.
_DEFAULT = object()


def get_default_auth_manager():
    return BasicPasswordAuthManager('user', 'randomdatapassword')


def get_default_route_mapping():
    return get_route_mapping()


def generate_wscontext(route_mapping=_DEFAULT, auth_manager=_DEFAULT):
    # Handle default authentication.
    if auth_manager is _DEFAULT:
        auth_manager = get_default_auth_manager()
    # Handle default routes.
    if route_mapping is _DEFAULT:
        route_mapping = get_default_route_mapping()
    auth_context = AuthContext(auth_manager, dict(user=auth_manager))
    return WsContext(
        auth_context, route_mapping, debug=get_unittest_debug(),
        other_parsers=[RawProxyParser()]
    )


class AsyncWsproxyTestCase(testing.AsyncHTTPTestCase):
    """Base class for testing with Wsproxy.

    This contains various "helpers" that will instantiate a wsproxy server
    with various settings. Some of the helpers can be overridden for more
    control.

    Override: 'create_wscontext()' for more control WsContext object for
    both the server and the client. A keyword argument of `client` will be
    passed when invoked indicating whether this is for a new client or a
    server.
    """

    def create_wscontext(self, client=True):
        return generate_wscontext()

    def get_app(self):
        """Generate the default 'wsproxy' app for use in the test.

        To customize the routes (for example), override 'get_routes()' and
        'get_wscontext()' as appropriate.
        """
        # This context is for the server.
        self.context = self.create_wscontext(client=False)

        return web.Application([
            (r'/ws', WsServerHandler, dict(context=self.context)),
            # (r'/client/details', InfoHandler, dict(context=self.context)),
            # (r'/client/(?P<cxn_id>[^/]+)', ClientInfoHandler, dict(context=self.context))
        ])

    async def ws_connect(self, protocol='ws', path='/ws'):
        client_context = self.create_wscontext()
        url = "{}://127.0.0.1:{}{}".format(protocol, self.get_http_port(), path)
        cxn = WsClientConnection(client_context, url)
        state = await cxn.open()
        return state


class EchoServer(tcpserver.TCPServer):
    """Dummy Server for testing purposes.

    It reads until it receives a newline, then echoes it back.
    """
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

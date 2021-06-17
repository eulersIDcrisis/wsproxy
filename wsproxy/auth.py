"""auth.py.

Module with basic authentication details for the proxy.
"""


class NotAuthorized(Exception):
    """Exception indicating that the request is not permitted."""


class AuthManager(object):
    """Manager to query whether certain operations are permitted."""

    def check_proxy_request(self, host, port, protocol):
        return True

    def check_json_route(self, route, auth_field):
        return True


class NoAccessAuthManager(AuthManager):

    def check_proxy_request(self, host, port, protocol):
        raise NotAuthorized("Request not authorized.")

    def check_route(self, route, auth_field):
        raise NotAuthorized("Route not authorized.")


class PortTunnelAuthManager(AuthManager):

    def __init__(self, port):
        self.port = port

    def check_proxy_request(self, host, port, protocol):
        if host == 'localhost' or host == '127.0.0.1' or host == '::1':
            if self.port == port:
                return
        raise NotAuthorized("Request not authorized.")

    def check_route(self, route, auth_field):
        return True
        # raise NotAuthorized("Route not authorized.")

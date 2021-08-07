"""context.py.

Authentication context information for wsproxy.

Authentication for this webproxy consists of an AuthManager, which
is responsible for logging a user in. When a user is logged in, they
are assigned some 'AuthContext', which indicates the operations this
user is allowed to perform.

The goal is to eventually make these AuthContext's configurable, so
that different levels of access are allowed.
"""
import ipaddress
from abc import ABCMeta, abstractmethod


class NotAuthorized(Exception):
    """Exception indicating that access is not authorized."""


class AuthContext(metaclass=ABCMeta):
    """Class that stores auth details for some connection.

    This class has the following methods:
     - check_json_route(self, route)
     - check_proxy_request(self, host, port, protocol)

    The 'check_json_route' flags whether certain (JSON) requests are
    valid; the arguments passed are the name of the route and some field.
    """

    def __init__(self, user='(nobody)'):
        self._user = user

    @property
    def subject(self):
        """Return the user registered for this AuthContext."""
        return self._subject

    @abstractmethod
    def check_proxy_request(self, host, port, protocol):
        return True

    @abstractmethod
    def check_json_route(self, route):
        return True


class NoAccessAuthContext(AuthContext):

    def check_proxy_request(self, host, port, protocol):
        raise NotAuthorized('Request not authorized.')

    def check_json_route(self, route):
        if route == 'proxy_socket_monitor':
            return True
        raise NotAuthorized('Request not authorized.')


class AllAccessAuthContext(AuthContext):

    def check_proxy_request(self, host, port, protocol):
        return True

    def check_json_route(self, route):
        return True


class CustomAccessAuthContext(AuthContext):

    def __init__(self, permit_localhost=True, permit_private_subnets=True):
        self._permit_localhost = permit_localhost
        self._permit_private_subnets = permit_private_subnets

    def check_json_route(self, route):
        return True

    def check_proxy_request(self, host, port, protocol):
        ip = ipaddress.ip_address(host)
        if not self._permit_localhost and ip.is_loopback:
            return False
        if not self._permit_private_subnets and ip.is_private:
            return False
        return True

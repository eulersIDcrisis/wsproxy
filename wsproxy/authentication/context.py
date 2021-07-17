"""context.py.

Authentication context information for wsproxy.

Authentication for this webproxy consists of an AuthManager, which
is responsible for logging a user in. When a user is logged in, they
are assigned some 'AuthContext', which indicates the operations this
user is allowed to perform.

The goal is to eventually make these AuthContext's configurable, so
that different levels of access are allowed.
"""
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
        raise NotAuthorized('Request not authorized.')


class AllAccessAuthContext(AuthContext):

    def check_proxy_request(self, host, port, protocol):
        return True

    def check_json_route(self, route):
        return True

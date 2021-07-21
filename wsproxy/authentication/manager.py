"""manager.py.

Implementation details for AuthManager and associated
authentication pieces.

Authentication works in this proxy as follows:
 - AuthManager reads credentials and returns AuthContext
 - AuthContext is queried to permit a request.

There is one AuthContext per (websocket) connection, as well
as for HTTP requests.
"""
import base64
from .context import AllAccessAuthContext, NoAccessAuthContext


class AuthManager(object):
    """Class that is responsible for assigning AuthContexts.

    This class receives input credentials and returns an applicable
    AuthContext for that connection.
    """

    def get_auth_context(self, auth_text):
        return NoAccessAuthContext()

    def get_default_auth_context(self):
        return NoAccessAuthContext()


class BasicPasswordAuthManager(AuthManager):
    """Class that assigns auth contexts based on username/passwords.

    For now, this only supports an all-or-nothing authentication.
    """

    def __init__(self, username, password):
        self.__username = username
        self.__password = password

    def get_auth_context(self, auth_text):
        data = u'{}@{}'.format(self.__username, self.__password).encode('utf8')
        if data == base64.b64decode(auth_text):
            return AllAccessAuthContext()
        return self.get_default_auth_context()

    def get_default_auth_context(self):
        return NoAccessAuthContext()

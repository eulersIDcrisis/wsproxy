"""manager.py.

Implementation details for AuthManager and associated
authentication pieces.

Authentication works in this proxy as follows:
 - AuthManager reads credentials and returns AuthContext
 - AuthContext is queried to permit a request.

There is one AuthContext per (websocket) connection, as well
as for HTTP requests.
"""
from .context import AllAccessAuthContext


class AuthManager(object):
    """Class that is responsible for assigning AuthContexts.

    This class receives input credentials and returns an applicable
    AuthContext for that connection.
    """

    def get_auth_context(self, jwt):
        return AllAccessAuthContext()

    def get_default_auth_context(self):
        return AllAccessAuthContext()

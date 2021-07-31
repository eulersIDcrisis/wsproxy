"""manager.py.

Implementation details for AuthManager and associated
authentication pieces.

Authentication works in this proxy as follows:
 - AuthManager reads credentials and returns AuthContext
 - AuthContext is queried to permit a request.

There is one AuthContext per (websocket) connection, as well
as for HTTP requests.
"""
import datetime
import jwt
from wsproxy.util import get_child_logger
from wsproxy.authentication.context import (
    AllAccessAuthContext, NoAccessAuthContext
)


logger = get_child_logger('auth')


class AuthManager(object):
    """Class that is responsible for assigning AuthContexts.

    This class receives input credentials and returns an applicable
    AuthContext for that connection.
    """

    def get_auth_context(self, auth_text):
        return AllAccessAuthContext()


class BasicPasswordAuthManager(AuthManager):
    """Class that assigns auth contexts based on username/passwords.

    For now, this only supports an all-or-nothing authentication.
    """
    DEFAULT_ALGORITHM = "HS256"

    def __init__(self, username, password):
        self.__username = username
        self.__password = password

    def get_auth_context(self, auth_text):
        try:
            print("DECODE: ", auth_text)
            # Decode the given data as a JWT that is signed with the password.
            jwt_dict = jwt.decode(auth_text, self.__password, algorithms=[
                BasicPasswordAuthManager.DEFAULT_ALGORITHM
            ])
            # Check the subject of the JWT.
            if jwt_dict.get('sub') != self.__username:
                raise ValueError("Invalid subject for JWT.")

            # TODO -- Check that the time range of this JWT is within bounds.

            return AllAccessAuthContext()
        except Exception as exc:
            logger.error("Invalid authentication: %s", exc)
        return NoAccessAuthContext()

    @staticmethod
    def create_jwt(username, password):
        """Create a JWT that authenticates with the given user/password."""
        jwt_dict = dict(
            iss='client',
            sub=username,
            iat=datetime.datetime.utcnow(),
            exp=datetime.datetime.utcnow() + datetime.timedelta(seconds=300)
        )
        return jwt.encode(
            jwt_dict, password,
            algorithm=BasicPasswordAuthManager.DEFAULT_ALGORITHM)

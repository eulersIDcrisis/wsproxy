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


def _empty_auth_callback(*args, **kwargs):
    """Default callback that returns an empty JWT token."""
    return ''


class AuthManager(object):
    """Class that is responsible for assigning AuthContexts.

    This class receives input credentials and returns an applicable
    AuthContext for that connection.
    """

    @staticmethod
    def get_all_access_context(*args, **kwargs):
        """Return an AllAccessAuthContext (i.e. ALWAYS succeeds).

        Use this with caution! This does not actually perform any
        verification and offers ABSOLUTELY NO GUARANTEES about any calls
        made!
        """
        return AllAccessAuthContext()

    @staticmethod
    def get_no_access_context(*args, **kwargs):
        """Return a NoAccessAuthContext (i.e. when authentication fails)."""
        return NoAccessAuthContext()

    def __init__(self, authenticate_callback=None, verify_callback=None):
        """Create an instance of 'AuthManager'.

        Parameters
        ----------
        authenticate_callback: None or function
            The callback invoked to authenticate with another target. This
            should either be 'None' or a function with the following
            signature: func(str) -> AuthContext
        verify_callback: None or function
            The callback invoked to send a verification token that verifies
            this instance with another target.
        """
        # JWTs to verify another instance.
        if not callable(authenticate_callback):
            self._get_auth_context = AuthManager.get_no_access_context
        else:
            self._get_auth_context = authenticate_callback

        # JWTs to verify this instance.
        if not callable(verify_callback):
            self._generate_auth_jwt = _empty_auth_callback
        else:
            self._generate_auth_jwt = verify_callback

    def generate_auth_jwt(self, *args, **kwargs):
        """Generate the auth JWT that verifies this instance of wsproxy."""
        try:
            return self._generate_auth_jwt(*args, **kwargs)
        except Exception:
            logger.exception("Error generating auth JWT!")
            return ''

    def get_auth_context(self, auth_text):
        """Return the auth context for the profile matching auth_text.

        The 'auth_text' field is expected to be a JWT token, with the
        semantics negotiated and shared externally.
        """
        try:
            return self._get_auth_context(auth_text)
        except Exception:
            logger.exception("Error fetching auth context!")
        return NoAccessAuthContext()


#
# Pre-defined callbacks for different authentication strategies.
#
class BasicPasswordAuthFactory:
    """Class with helpers to generate AuthManagers that use passwords.

    This has calls to return an AuthManager with various fields.
    """

    DEFAULT_ALGORITHM = "HS256"

    def __init__(self, username, password):
        self.__username = username
        self.__password = password

    def get_auth_context(self, auth_text):
        try:
            # Decode the given data as a JWT that is signed with the password.
            jwt_dict = jwt.decode(auth_text, self.__password, algorithms=[
                BasicPasswordAuthFactory.DEFAULT_ALGORITHM
            ])
            # Check the subject of the JWT.
            user = jwt_dict.get('sub')
            if user != self.__username:
                raise ValueError("Invalid subject for JWT.")

            # TODO -- Check that the time range of this JWT is within bounds.
            return AllAccessAuthContext(user)
        except Exception as exc:
            logger.error("Error in get_auth_context (BasicPassword): %s", exc)
        return NoAccessAuthContext()

    def generate_auth_jwt(self, msg):
        """Create a JWT that authenticates with the given user/password."""
        now = datetime.datetime.utcnow()
        jwt_dict = dict(
            iss='client',
            sub=self.__username,
            iat=now,
            nbf=now - datetime.timedelta(seconds=5),
            exp=now + datetime.timedelta(seconds=300)
        )
        return jwt.encode(
            jwt_dict, self.__password,
            algorithm=BasicPasswordAuthFactory.DEFAULT_ALGORITHM)

    def create_auth_manager(self):
        """Return the AuthManager that handle these passwords."""
        return AuthManager(self.get_auth_context, self.generate_auth_jwt)

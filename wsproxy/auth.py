"""auth.py.

Primary Authentication Module for wsproxy.

This module defines the primary objects and functions for
authentication with wsproxy.
"""
from abc import ABCMeta, abstractmethod
import datetime
import jwt


class NotAuthorized(Exception):
    """Exception indicating access is not authorized."""


ALL = object()


class AuthManager(metaclass=ABCMeta):
    """Manager that indicates what routes are permitted."""

    def __init__(self, json_routes=ALL, permit_localhost=False,
                 permit_private_subnets=False):
        self._json_routes = json_routes
        self._permit_localhost = permit_localhost
        self._permit_private_subnets = permit_private_subnets

    def check_json_route(self, route):
        if self._json_routes is ALL:
            return True
        return bool(route in self._json_routes)

    def check_proxy_request(self, host, port, protocol):
        raise NotAuthorized()

    @abstractmethod
    def get_subject(self):
        """Return the subject this AuthManager pertains to."""
        return ''

    @abstractmethod
    def authenticate(self, token):
        """Given a JWT token, return whether it is valid for this AuthManager.

        NOTE: This should raise an exception or return False if authentication
        fails.
        """
        raise NotAuthorized()

    @abstractmethod
    def generate_auth_jwt(self, token):
        """Generate a JWT token identifying this AuthManager."""
        return ''


class NoAccessAuthManager(AuthManager):
    """Create an AuthManager that rejects every request."""

    def __init__(self, subject):
        super(NoAccessAuthManager, self).__init__()
        self.subject = subject

    def authenticate(self, token):
        raise NotAuthorized('Authentication failed!')

    def get_subject(self):
        return self.subject

    def generate_auth_jwt(self, token):
        return ''


class AuthContext(object):
    """Primary Context that stores the users authorized for wsproxy."""

    def __init__(self, local_manager, auth_manager_mapping=None):
        self._local_manager = local_manager
        self._auth_manager_mapping = {
            user: manager
            for user, manager in (auth_manager_mapping or {}).items()
        }

    @property
    def user(self):
        return self._local_manager.get_subject()

    def generate_auth_jwt(self, token):
        return self._local_manager.generate_auth_jwt(token)

    def authenticate(self, token):
        subject = ''
        try:
            # Extract the 'sub' claim from the JWT and see if it matches one
            # of the users here.
            body = jwt.decode(token, '', options=dict(verify_signature=False))
            subject = body['sub']
            # Extract the applicable auth manager.
            manager = self._auth_manager_mapping[subject]

            # Verify that this JWT has an expiration and "not-before" that
            # is reasonable (i.e. only valid for 10 minutes).
            if not body.get('nbf') or not body.get('exp'):
                raise Exception("Missing required fields!")
            time_range = (body['exp'] - body['nbf'])
            if time_range > 600:
                raise Exception('JWT is valid for too large a time!')

            if not manager.authenticate(token):
                raise Exception("Failed Authentication!")
            return manager
        except Exception as e:
            raise NotAuthorized() from e

    def add_auth_manager(self, manager):
        subject = manager.get_subject()
        if subject in self._auth_manager_mapping:
            raise KeyError("'subject' already exists!")
        self._auth_manager_mapping[subject] = manager


class BasicPasswordAuthManager(AuthManager):

    DEFAULT_ALGORITHM = 'HS256'

    def __init__(self, username, password, json_routes=ALL, permit_localhost=False,
                 permit_private_subnets=False):
        super(BasicPasswordAuthManager, self).__init__(
            json_routes, permit_localhost, permit_private_subnets
        )
        self.__username = username
        self.__password = password

    def authenticate(self, token):
        """Given a JWT token, return whether it is valid for this AuthManager.

        NOTE: This should raise an exception or return False if authentication
        fails.
        """
        try:
            args = jwt.decode(
                token, self.__password, algorithms=[self.DEFAULT_ALGORITHM])
            return True
        except Exception as e:
            raise NotAuthorized from e

    def get_subject(self):
        return self.__username

    def generate_auth_jwt(self, token):
        """Generate a JWT token identifying this AuthManager."""
        now = datetime.datetime.utcnow()
        jwt_dict = dict(
            iss=self.__username,
            sub=self.__username,
            iat=now,
            nbf=now - datetime.timedelta(seconds=5),
            exp=now + datetime.timedelta(seconds=300)
        )
        return jwt.encode(
            jwt_dict, self.__password,
            algorithm=self.DEFAULT_ALGORITHM)


class SSHKeyAuthManager(AuthManager):

    def __init__(self, public_key, private_key=None, json_routes=ALL,
                 permit_localhost=False, permit_private_subnets=False):
        super(BasicPasswordAuthManager, self).__init__(
            json_routes, permit_localhost, permit_private_subnets
        )
        self.__username = username
        self.__password = password

    def authenticate(self, token):
        """Given a JWT token, return whether it is valid for this AuthManager.

        NOTE: This should raise an exception or return False if authentication
        fails.
        """
        try:
            args = jwt.decode(
                token, self.__password, algorithms=[self.DEFAULT_ALGORITHM])
            return True
        except Exception as e:
            raise NotAuthorized from e

    def get_subject(self):
        return ''

    def generate_auth_jwt(self, token):
        """Generate a JWT token identifying this AuthManager."""
        now = datetime.datetime.utcnow()
        jwt_dict = dict(
            iss=self.__username,
            sub=self.__username,
            iat=now,
            nbf=now - datetime.timedelta(seconds=5),
            exp=now + datetime.timedelta(seconds=300)
        )
        return jwt.encode(jwt_dict, self.__password)

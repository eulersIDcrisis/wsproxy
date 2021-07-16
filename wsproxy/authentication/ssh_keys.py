"""ssh_keys.py.

Authentication module to support using SSH keys.
"""
import os
from cryptography.hazmat.primitives.serialization import (
    load_ssh_public_key, load_ssh_private_key
)
from wsproxy.util import get_child_logger


logger = get_child_logger('ssh')


_DEFAULT = object()


class SSHAuthManager(object):

    def __init__(self, local_private_key, authorized_public_keys):
        self.__private_key = local_private_key
        self.__public_keys = authorized_public_keys

    def private_key(self):
        return self.__private_key

    def public_keys(self):
        return self.__public_keys


def load_ssh_auth_manager(private_key_path=_DEFAULT, authorized_key_path=_DEFAULT):
    user_dir = os.path.expanduser('~')
    authorized_keys = []

    # Load the authorized keys first.
    if authorized_key_path is _DEFAULT:
        authorized_key_path = os.path.join(user_dir, '.ssh', 'authorized_keys')
    if not authorized_key_path:
        authorized_key_path = ''
    if os.path.exists(authorized_key_path):
        with open(authorized_key_path, 'rb') as stm:
            # Read each line as an authorized key.
            for line in stm:
                try:
                    key = load_ssh_public_key(line)
                    authorized_keys.append(key)
                except Exception as exc:
                    logger.warning("Skipping key due to error: %s", exc)

    # Load the private key to use here.
    if private_key_path is _DEFAULT:
        private_key_path = os.path.join(user_dir, '.ssh', 'id_ecdsa')
        if not os.path.exists(private_key_path):
            private_key_path = os.path.join(user_dir, '.ssh', 'id_rsa')
    if not private_key_path:
        private_key_path = ''
    if os.path.exists(private_key_path):
        with open(private_key_path, 'rb') as stm:
            private_key = load_ssh_private_key(stm.read(), password=None)

    return SSHAuthManager(private_key, authorized_keys)

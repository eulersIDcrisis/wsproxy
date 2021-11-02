"""auth_tests.py.

Test cases for wsproxy.auth module.
"""
import unittest
from wsproxy import auth

class AuthManagerTests(unittest.TestCase):

    def test_basic_host_port_regexes(self):
        manager1 = auth.BasicPasswordAuthManager(
            'admin', 'password', allowed_hosts=auth.ALL)
        self.assertTrue(manager1.check_proxy_request('www.test.com', 80))
        self.assertTrue(manager1.check_proxy_request('127.0.0.1', 80))
        self.assertTrue(manager1.check_proxy_request('localhost', 8080))

        manager2 = auth.BasicPasswordAuthManager(
            'admin', 'password', allowed_hosts=['localhost:8080'])
        self.assertFalse(manager2.check_proxy_request('www.test.com', 80))
        self.assertFalse(manager2.check_proxy_request('127.0.0.1', 80))
        # Test the wrong port fails.
        self.assertFalse(manager2.check_proxy_request('localhost', 9080))
        self.assertTrue(manager2.check_proxy_request('localhost', 8080))



if __name__ == '__main__':
    unittest.main()

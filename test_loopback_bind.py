"""A loopback service must bind without asking any resolver for a hostname."""
from http.server import BaseHTTPRequestHandler
import unittest
from unittest.mock import patch

from server import ForegroundHTTPServer


class LoopbackBindTests(unittest.TestCase):
    def test_unavailable_reverse_dns_cannot_delay_local_bind(self):
        with patch('socket.getfqdn', side_effect=AssertionError('Unexpected reverse DNS')) as fqdn:
            server = ForegroundHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
            try:
                self.assertEqual(server.server_name, 'localhost')
                self.assertEqual(server.server_address[0], '127.0.0.1')
                self.assertEqual(server.server_port, server.server_address[1])
                self.assertGreater(server.server_port, 0)
                fqdn.assert_not_called()
            finally:
                server.server_close()


if __name__ == '__main__':
    unittest.main()

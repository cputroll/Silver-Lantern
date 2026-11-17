import unittest
from unittest.mock import patch
from test_game_sync import h


class StallPolicyTests(unittest.TestCase):
    def test_checksum_body_stall_retries_once(self):
        from unittest.mock import Mock
        first, second = Mock(), Mock()
        first.iter_content.side_effect = TimeoutError('stall')
        second.iter_content.return_value = [(b'a' * 64) + b'  HeyListen-Get.zip']
        client = Mock()
        client.get.side_effect = [first, second]
        with patch.object(h, 'say') as say:
            self.assertEqual(h.fetch_update_checksum(client, 'https://example.test/sums'), 'a' * 64)
        self.assertEqual(client.get.call_count, 2)
        say.assert_called_once_with('Connection stalled — retrying…')

    def test_checksum_uses_metadata_total_deadline(self):
        from unittest.mock import Mock
        from curl_cffi import CurlOpt
        response = Mock()
        response.iter_content.return_value = [(b'a' * 64) + b'  HeyListen-Get.zip']
        client = Mock()
        client.get.return_value = response
        with patch.object(h, 'checksum_session') as factory:
            factory.return_value.__enter__.return_value = client
            self.assertEqual(h.fetch_update_checksum(h.session(), 'https://example.test/sums'), 'a' * 64)
        self.assertEqual(client.get.call_args.kwargs['timeout'], (10, 60))
        with patch.object(h.requests, 'Session') as session:
            h.checksum_session()
        options = session.call_args.kwargs['curl_options']
        self.assertEqual(options[CurlOpt.TIMEOUT_MS], 60000)
        self.assertEqual(options[CurlOpt.MAXFILESIZE_LARGE], 65536)

    def test_metadata_retries_once_with_visible_message(self):
        from unittest.mock import Mock
        client = Mock()
        client.get.side_effect = [TimeoutError('stall'), Mock()]
        with patch.object(h, 'say') as say:
            h.metadata_get(client, 'https://example.test')
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.get.call_args.kwargs['timeout'], (10, 60))
        say.assert_called_once_with('Connection stalled — retrying…')

    def test_stream_session_uses_low_speed_without_total_timeout(self):
        from curl_cffi import CurlOpt
        with patch.object(h.requests, 'Session') as factory:
            h.stream_session()
        options = factory.call_args.kwargs['curl_options']
        self.assertEqual(options[CurlOpt.LOW_SPEED_LIMIT], 1024)
        self.assertEqual(options[CurlOpt.LOW_SPEED_TIME], 30)
        self.assertEqual(options[CurlOpt.CONNECTTIMEOUT_MS], 10000)
        self.assertEqual(options[CurlOpt.TIMEOUT_MS], 0)

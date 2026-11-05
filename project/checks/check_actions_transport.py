"""Protocol regression checks; no running game or real socket is needed."""

import json
import unittest
from unittest.mock import Mock, patch

from project.game.actions import ActionProxy


class ActionTransportChecks(unittest.TestCase):
    def request(self, chunks, command="observe_v2%state:3"):
        connection = Mock()
        connection.connect_ex.return_value = 0
        connection.recv.side_effect = chunks
        proxy = ActionProxy(10783)
        proxy.mmap_reader = Mock()
        with patch("project.game.actions.socket.socket", return_value=connection):
            response = proxy._post_message(command)
        connection.close.assert_called_once()
        return response, proxy, connection

    def test_split_terminator_uses_socket_for_state(self):
        response, proxy, _ = self.request([b'{"Player":{}}<E', b'OF>'])
        self.assertEqual(json.loads(response), {"Player": {}})
        proxy.mmap_reader.read_from_mmap.assert_not_called()

    def test_unicode_split_across_packets(self):
        payload = '{"name":"农夫"}'.encode("utf-8")
        split = payload.index("农".encode("utf-8")) + 1
        response, _, _ = self.request([payload[:split], payload[split:] + b"<EOF>"])
        self.assertEqual(json.loads(response)["name"], "农夫")

    def test_full_image_response_still_supported(self):
        payload = b'{"ScreenShot":"' + b'x' * 5_000_000 + b'"}'
        wire = payload + b"<EOF>"
        chunks = [wire[index:index + 65536] for index in range(0, len(wire), 65536)]
        response, _, _ = self.request(chunks, "observe_v2%3")
        self.assertEqual(response.encode("utf-8"), payload)

    def test_old_observe_still_uses_shared_memory(self):
        proxy = ActionProxy(10783)
        proxy.mmap_reader = Mock()
        proxy.mmap_reader.read_from_mmap.return_value = {"legacy": True}
        connection = Mock()
        connection.connect_ex.return_value = 0
        with patch("project.game.actions.socket.socket", return_value=connection):
            self.assertEqual(proxy._post_message("observe%3"), {"legacy": True})
        connection.recv.assert_not_called()
        connection.close.assert_called_once()

    def test_incomplete_response_is_not_returned_as_valid(self):
        with patch("builtins.print"):
            response, _, _ = self.request([b'{"incomplete":', b''])
        self.assertIsNone(response)

    def test_state_method_validates_and_sends_radius(self):
        proxy = ActionProxy(10783)
        with patch.object(proxy, "_post_message", return_value="{}") as send:
            self.assertEqual(proxy.observe_state(12), "{}")
            send.assert_called_once_with("observe_v2%state:12")
        for radius in [-1, 65, "3"]:
            with self.assertRaises(ValueError):
                proxy.observe_state(radius)

    def test_explicit_menu_and_world_observations_use_existing_transport(self):
        proxy = ActionProxy(10783)
        with patch.object(proxy, "_post_message", return_value='{"available":true}') as send:
            proxy.observe_menu()
            send.assert_called_with("observe_v2%menu")
            proxy.observe_world_map()
            send.assert_called_with("observe_v2%world")
            proxy.observe_crops()
            send.assert_called_with("observe_v2%crops")


if __name__ == "__main__":
    unittest.main()

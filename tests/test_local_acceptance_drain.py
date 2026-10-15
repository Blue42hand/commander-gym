import unittest
from unittest.mock import Mock, patch
from scripts.local_inference_acceptance_sidecar import drain_capture


class LocalAcceptanceDrainTests(unittest.TestCase):
    def test_waits_for_background_finalizer_before_success(self):
        capture = Mock(errors={})
        capture.operational_metrics.side_effect = [
            {"pendingRecordWrites": 3}, {"pendingRecordWrites": 1}, {"pendingRecordWrites": 0}]
        with patch("scripts.local_inference_acceptance_sidecar.time.sleep") as sleep:
            self.assertEqual(drain_capture(capture), {"pendingRecordWrites": 0})
        self.assertEqual(capture.scan.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        capture.close.assert_not_called()

    def test_failure_and_expired_drain_do_not_claim_success(self):
        capture = Mock(errors={"fixture": "JournalError"})
        with self.assertRaises(RuntimeError):
            drain_capture(capture)
        capture.errors = {}
        capture.operational_metrics.return_value = {"pendingRecordWrites": 1}
        with patch("scripts.local_inference_acceptance_sidecar.time.monotonic", side_effect=[0, 121]):
            with self.assertRaises(TimeoutError):
                drain_capture(capture)


if __name__ == "__main__":
    unittest.main()

"""Native resource creation, calls and disposal share one owner thread."""
import threading
import unittest

from uni_robot_agent.services.environment.owner import OwnedAdapter


class OwnerTests(unittest.TestCase):
    def test_lifecycle_and_errors_stay_on_owner(self):
        calls = []

        class Native:
            description = object()

            def __init__(self):
                calls.append(("create", threading.get_ident()))

            def step(self, *, fail=False):
                calls.append(("step", threading.get_ident()))
                if fail:
                    raise ValueError("native failure")
                return 7

            def close(self):
                calls.append(("close", threading.get_ident()))

        owner = OwnedAdapter(Native)
        self.assertEqual(owner.step(), 7)
        with self.assertRaisesRegex(ValueError, "native failure"):
            owner.step(fail=True)
        owner.close()
        owner.close()
        self.assertEqual([name for name, _ in calls], ["create", "step", "step", "close"])
        self.assertEqual(len({thread for _, thread in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())

    def test_failed_creation_does_not_leave_an_owner_thread(self):
        threads = set(threading.enumerate())

        def fail():
            raise RuntimeError("creation failed")

        with self.assertRaisesRegex(RuntimeError, "creation failed"):
            OwnedAdapter(fail)
        self.assertEqual(set(threading.enumerate()), threads)


if __name__ == "__main__":
    unittest.main()

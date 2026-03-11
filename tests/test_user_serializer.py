import threading
import time
import unittest

from services.user_serializer import UserOperationSerializer


class UserOperationSerializerTests(unittest.TestCase):
    def test_serialize_blocks_same_user(self) -> None:
        serializer = UserOperationSerializer()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first_step() -> None:
            with serializer.serialize(1):
                first_entered.set()
                release_first.wait(timeout=2)

        def second_step() -> None:
            with serializer.serialize(1):
                second_entered.set()

        first_thread = threading.Thread(target=first_step)
        second_thread = threading.Thread(target=second_step)
        first_thread.start()
        first_entered.wait(timeout=1)

        second_thread.start()
        # second thread must wait until the first releases the lock
        self.assertFalse(second_entered.wait(timeout=0.2))
        release_first.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

        self.assertTrue(second_entered.is_set())

    def test_serialize_allows_different_users(self) -> None:
        serializer = UserOperationSerializer()
        events = [threading.Event(), threading.Event()]

        def worker(user_id: int, idx: int) -> None:
            with serializer.serialize(user_id):
                events[idx].set()
                time.sleep(0.1)

        threads = [
            threading.Thread(target=worker, args=(1, 0)),
            threading.Thread(target=worker, args=(2, 1)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertTrue(all(event.is_set() for event in events))


if __name__ == "__main__":
    unittest.main()

import unittest
import threading

from offline_publication_recovery import Store, Publisher, Conflict, Ambiguous


class RecoveryTests(unittest.TestCase):
    def test_fresh_publish_and_interrupted_upload_recovery(self):
        store = Store()
        publisher = Publisher(store, '4.0', {'game.zip': b'game'})
        publisher.prepare()
        self.assertIsNone(store.channel)
        publisher.recover()
        self.assertEqual(store.channel['version'], '4.0')
        self.assertEqual(store.verify_channel(), '4.0')

    def test_ambiguous_cas_is_reconciled_without_rollback(self):
        store = Store()
        publisher = Publisher(store, '4.0', {'game.zip': b'game'})
        store.ambiguous_after_commit = True
        publisher.recover()
        self.assertEqual(store.verify_channel(), '4.0')
        publisher.recover()
        self.assertEqual(store.generation, 1)

    def test_competing_actors_do_not_replace_newer_channel(self):
        store = Store()
        old = Publisher(store, '4.0', {'game.zip': b'old'})
        new = Publisher(store, '5.0', {'game.zip': b'new'})
        old.prepare()
        new.recover()
        with self.assertRaises(Conflict):
            old.recover()
        self.assertEqual(store.verify_channel(), '5.0')

    def test_concurrent_cas_has_only_one_winner(self):
        store = Store()
        actors = [Publisher(store, v, {'game.zip': v.encode()}) for v in ('4.0', '5.0')]
        for actor in actors:
            actor.prepare()
        barrier = threading.Barrier(2)
        results = []
        def attempt(actor):
            generation, _ = store.snapshot()
            barrier.wait(timeout=5)
            try:
                store.cas(generation, actor.pointer)
                results.append('won')
            except Conflict:
                results.append('lost')
        threads = [threading.Thread(target=attempt, args=(actor,)) for actor in actors]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ['won', 'lost'])
        self.assertEqual(store.generation, 1)
        store.verify_channel()

    def test_immutable_collision_and_corruption_fail_closed(self):
        store = Store()
        store.put('asset', b'one')
        with self.assertRaises(Conflict):
            store.put('asset', b'two')
        publisher = Publisher(store, '4.0', {'game.zip': b'game'})
        publisher.prepare()
        store.assets[publisher.bundle_names[0]] = b'corrupted'
        with self.assertRaises(Conflict):
            publisher.recover()
        self.assertIsNone(store.channel)

    def test_ambiguous_before_commit_can_be_retried(self):
        store = Store()
        store.ambiguous_before_commit = True
        Publisher(store, '4.0', {'game.zip': b'game'}).recover()
        self.assertEqual(store.verify_channel(), '4.0')
        self.assertEqual(store.generation, 1)


if __name__ == '__main__':
    unittest.main()

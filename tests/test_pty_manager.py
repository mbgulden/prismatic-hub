import unittest
import time
import uuid
import os
from prismatic.pty_manager import RunManager

class TestPtyManager(unittest.TestCase):
    def setUp(self):
        self.manager = RunManager()

    def test_run_creation_and_output(self):
        run_id = str(uuid.uuid4())
        run = self.manager.create_run(run_id, command="echo 'test output'")
        
        output_chunks = []
        def on_output(data):
            output_chunks.append(data)
        
        run.attach(on_output)
        
        # Give it a moment to run
        timeout = 5
        start_time = time.time()
        while b'test output' not in b''.join(output_chunks) and (time.time() - start_time) < timeout:
            time.sleep(0.1)
            
        self.assertIn(b'test output', b''.join(output_chunks))
        run.pty.cleanup()

    def test_reconnect_replay(self):
        run_id = str(uuid.uuid4())
        run = self.manager.create_run(run_id, command="echo 'line1'; sleep 1; echo 'line2'")
        
        # Wait for first line to be in buffer
        time.sleep(1.0)
        
        output1 = []
        run.attach(lambda d: output1.append(d))
        self.assertIn(b'line1', b''.join(output1))
        
        # Detach
        run.clients.clear()
        
        # Reconnect later
        time.sleep(1.5) # Wait for line2
        output2 = []
        run.attach(lambda d: output2.append(d))
        
        full_output = b''.join(output2)
        self.assertIn(b'line1', full_output)
        self.assertIn(b'line2', full_output)
        run.pty.cleanup()

    def test_ttl_expiry(self):
        # We'll manually check the is_expired logic
        run_id = str(uuid.uuid4())
        run = self.manager.create_run(run_id, command="sleep 10", ttl=1)
        
        self.assertFalse(run.is_expired(), "Run should not be expired immediately")
        
        time.sleep(1.5)
        self.assertTrue(run.is_expired(), "Run should be expired after TTL")
        run.pty.cleanup()

if __name__ == "__main__":
    unittest.main()

import json
import pathlib
import subprocess
import tempfile
import unittest

class ProgressTest(unittest.TestCase):
    def test_progress_counters_and_atomic_final_report(self):
        with tempfile.TemporaryDirectory() as td:
            scanner = pathlib.Path(td) / 'hstscan'
            subprocess.run(['cc','-O2','-Wall','-Wextra','-pthread','-std=gnu11','-o',str(scanner),str(pathlib.Path(__file__).with_name('hstscan.c'))],check=True)
            root = pathlib.Path(td) / 'root'; root.mkdir()
            (root / 'file').write_bytes(b'a'*8192)
            progress = pathlib.Path(td) / 'progress.json'; output = pathlib.Path(td) / 'snapshot.json'
            result = subprocess.run([str(scanner),'--threads','2','--run-id','abc','--group','fast','--progress',str(progress),'--out',str(output),str(root)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            data = json.loads(progress.read_text()); snapshot = json.loads(output.read_text())
            self.assertEqual(set(data), set('run_id group updated_unix bytes files dirs roots_completed roots_total'.split()))
            self.assertEqual((data['run_id'], data['group']), ('abc', 'fast'))
            self.assertEqual(data['roots_completed'],1)
            self.assertEqual(data['roots_total'],1)
            self.assertEqual(data['files'],1)
            self.assertEqual(data['bytes'],snapshot['mounts'][0]['scanned_bytes'])
            self.assertLess(progress.stat().st_size,8192)
            self.assertFalse(list(pathlib.Path(td).glob('progress.json.*')))

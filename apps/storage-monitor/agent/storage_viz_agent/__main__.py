from pathlib import Path
import sys

from agent.release_cli import main

sys.exit(main(sys.argv[1:], str(Path(__file__).resolve().parents[2])))

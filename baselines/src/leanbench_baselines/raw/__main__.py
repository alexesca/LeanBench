from __future__ import annotations

import sys

from leanbench_baselines.common.server import main
from leanbench_baselines.raw.server import RawRepositoryServer

if __name__ == "__main__":
    sys.exit(main(RawRepositoryServer))
